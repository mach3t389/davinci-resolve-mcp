"""Command line for the engine. Read-only unless a pass is requested.

  python -m montage_engine.cli check "<timeline>" [--type interview] [--series masterclass] [--waveform]
                                [--editor "<timeline>"] [--json]
  python -m montage_engine.cli words "<timeline>" <store folder> [--from 00:10 --to 00:40]
  python -m montage_engine.cli pass <plan.json> [--apply] [--type interview] [--series masterclass] [--pauses <store>]
      --pauses: also shorten the pauses between sentences to cuts.keep_pause_seconds (words from the transcripts).
      The sound rules of the directives (J cuts, loudness) are applied to the NEW copy when they are set.
      plan.json = {"SRC": "<timeline>", "edits": [{"P": 100, "Q": 124, "D": 0, "text": "..."}], "NEW": "<optional>"}
      Without --apply: only the plan (new length, versions). With --apply: a NEW numbered copy is created from SRC
      (never SRC itself), fades/isolation restored, traceability markers, jump cuts created by the pass marked in
      yellow, and a before/after report is printed. SRC must be the highest version and unchanged since the plan.
Run with the davinci-resolve-mcp virtualenv (Resolve must be open with External scripting enabled).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from montage_engine import anchors, audio_rules, checks, panels, passes  # noqa: E402
from montage_engine.model import snapshot_from_resolve, tc  # noqa: E402
from montage_engine.series import (SERIES, VIDEO_TYPES, explain, missing_roles, resolve_directives,  # noqa: E402
                                   role_of, roles_of)


def _timeline(name):
    import resolve_safe_mcp as r
    _, p = r.project()
    ts = {t.GetName(): t for t in r.timelines(p)}
    if name not in ts:
        raise SystemExit(f'No timeline named "{name}". Versions: {sorted(ts)}')
    return ts[name]


def cmd_check(a):
    values, prov = resolve_directives(type_=VIDEO_TYPES.get(a.type), series=SERIES.get(a.series))
    snap = snapshot_from_resolve(_timeline(a.timeline))
    editor = snapshot_from_resolve(_timeline(a.editor)) if a.editor else None
    profiles = panels.profiles_for_snapshot(snap, role_of, measure=a.measure_alpha)
    report = checks.run_all(snap, values, with_waveform=a.waveform, editor=editor, profiles=profiles)
    report['fade_profiles'] = {'sidecar': sum(p.get('source') == 'sidecar' for p in profiles.values()),
                               'measured': sum(p.get('source') == 'measured' for p in profiles.values()),
                               'partial_overlays': sum(p.get('coverage') == 'partial' for p in profiles.values())}
    report['roles'] = {f'{k}{i}': v for (k, i), v in roles_of(snap).items()}
    report['missing_roles'] = missing_roles(snap)
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return
    print(f'== {snap.name}  (In-Out to {tc(snap.out_frame, snap.fps)})  type={a.type} series={a.series}')
    print('roles:', report['roles'])
    if report['missing_roles']:
        print('missing roles:', report['missing_roles'])
    print('fade information:', report['fade_profiles'])
    print('camera share:', {f'V{k}': f'{v:.0%}' for k, v in report['camera_share'].items()})
    for line in report['lines']:
        print(line)
    print('OK' if report['ok'] else f'{report["errors"]} error(s)', report['counts'])
    if a.explain:
        print('\n'.join(explain(values, prov)))


def cmd_pass(a):
    with open(a.plan, encoding='utf8') as f:
        plan = json.load(f)
    import resolve_safe_mcp as r
    resolve, p = r.project()
    ts = {t.GetName(): t for t in r.timelines(p)}
    src = ts.get(plan['SRC'])
    if src is None:
        raise SystemExit(f'No timeline named "{plan["SRC"]}".')
    base = (passes.parse_version(plan['SRC']) or (plan['SRC'],))[0]
    plan.setdefault('NEW', passes.next_version_name(list(ts), base, plan.get('action', 'passe moteur')))
    plan.setdefault('expect_end', int(src.GetEndFrame() - src.GetStartFrame()))
    end = plan['expect_end']
    video_rules = json.loads(a.video_rules) if a.video_rules else None
    values, _ = resolve_directives(type_=VIDEO_TYPES.get(a.type), series=SERIES.get(a.series), video=video_rules)
    if a.pauses:
        snap_src = snapshot_from_resolve(src)
        words, missing = anchors.place_words(snap_src, anchors.TranscriptStore(a.pauses))
        busy = [(e['P'], max(e['Q'], e['P'] + 1)) for e in plan['edits']]
        planned = audio_rules.plan_pause_trims(words, values, camera_runs=audio_rules.camera_runs(snap_src))
        trims = [e for e in audio_rules.outside_overlays(planned, snap_src, lambda n: role_of(n).startswith('camera'))
                 if not any(e['P'] < b and a0 < e['Q'] for a0, b in busy)]
        print(f'pauses raccourcissables (changement de camera possible, plans assez longs): {len(planned)}, sous un panneau ou un plan d appui (laissees): {len(planned) - len(trims)}')
        plan['edits'] = plan['edits'] + trims
        print(f'pauses raccourcies: {len(trims)} (sources sans transcription: {missing})')
    print('regles du son:', {k: v for k, v in dict(
        pause=values.get('cuts', {}).get('keep_pause_seconds'), coupe_j=values.get('cuts', {}).get('audio_lead_frames'),
        lufs=values.get('audio', {}).get('target_lufs')).items() if v is not None} or 'aucune')
    kept, new_end, fwd, back = passes.build_map(plan['edits'], end)
    print(f'{plan["SRC"]} -> {plan["NEW"]}: {end} -> {new_end} images ({new_end - end:+d}, {(new_end - end) / 24:+.1f} s), '
          f'{len(plan["edits"])} modification(s)')
    if not a.apply:
        print('(a blanc: rien n\'est cree; relancer avec --apply)')
        return
    cur = p.GetCurrentTimeline()
    before = snapshot_from_resolve(src)
    prof_b = panels.profiles_for_snapshot(before, role_of)
    rep_b = checks.run_all(before, values, profiles=prof_b)
    work = str(Path(a.work).resolve())
    try:
        res = passes.run_pass(resolve, p, plan, work, values=values)
        print('passe:', json.dumps(res, ensure_ascii=False))
        new = {t.GetName(): t for t in r.timelines(p)}[plan['NEW']]
        print('restauration:', json.dumps(passes.restore_after_import(p, src, new), ensure_ascii=False, default=str))
        with open(Path(work) / 'pass_map.json', encoding='utf8') as f:
            info = json.load(f)
        print('marqueurs de tracabilite:', passes.pass_markers(new, info))
        after = snapshot_from_resolve(new)
        rep_a = checks.run_all(after, values, profiles=panels.profiles_for_snapshot(after, role_of))
        created = passes.new_findings(rep_b['findings'], rep_a['findings'], back)
        print('controles avant :', rep_b['counts'])
        print('controles apres :', rep_a['counts'])
        print(f'{len(created)} defaut(s) cree(s) par la passe, marques en jaune:', passes.mark_findings(new, created))
        for line in created[:20]:
            print('  ', tc(line['frame'], after.fps), line['check'], line['message'])
    finally:
        if cur is not None:
            p.SetCurrentTimeline(cur)          # the editor's timeline stays open as it was


def cmd_words(a):
    snap = snapshot_from_resolve(_timeline(a.timeline))
    words, missing = anchors.place_words(snap, anchors.TranscriptStore(a.store))
    print(f'{len(words)} words placed; sources without transcript: {missing}')
    for w in words[:a.limit]:
        print(tc(w['f0'], snap.fps), w['w'])


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check')
    c.add_argument('timeline')
    c.add_argument('--type')
    c.add_argument('--series')
    c.add_argument('--waveform', action='store_true')
    c.add_argument('--editor')
    c.add_argument('--json', action='store_true')
    c.add_argument('--explain', action='store_true')
    c.add_argument('--measure-alpha', action='store_true', help='measure the alpha with ffmpeg for renders without a sidecar (slow)')
    c.set_defaults(func=cmd_check)
    q = sub.add_parser('pass')
    q.add_argument('plan')
    q.add_argument('--apply', action='store_true')
    q.add_argument('--type')
    q.add_argument('--series')
    q.add_argument('--work', default='work/pass')
    q.add_argument('--video-rules', help='JSON directive layer for this video, e.g. {"cuts": {"audio_lead_frames": 6}}')
    q.add_argument('--pauses', metavar='STORE', help='transcripts folder: shorten pauses to cuts.keep_pause_seconds')
    q.set_defaults(func=cmd_pass)
    w = sub.add_parser('words')
    w.add_argument('timeline')
    w.add_argument('store')
    w.add_argument('--limit', type=int, default=40)
    w.set_defaults(func=cmd_words)
    a = ap.parse_args(argv)
    a.func(a)


if __name__ == '__main__':
    main()

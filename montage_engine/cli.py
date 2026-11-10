"""Command line for the engine. Read-only unless a pass is requested.

  python -m montage_engine.cli check "<timeline>" [--type interview] [--series masterclass] [--waveform]
                                [--editor "<timeline>"] [--json]
  python -m montage_engine.cli words "<timeline>" <store folder> [--from 00:10 --to 00:40]
Run with the davinci-resolve-mcp virtualenv (Resolve must be open with External scripting enabled).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from montage_engine import anchors, checks, panels  # noqa: E402
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
    w = sub.add_parser('words')
    w.add_argument('timeline')
    w.add_argument('store')
    w.add_argument('--limit', type=int, default=40)
    w.set_defaults(func=cmd_words)
    a = ap.parse_args(argv)
    a.func(a)


if __name__ == '__main__':
    main()

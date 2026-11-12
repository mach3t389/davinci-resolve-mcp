"""Generic edit pass (doc 25, step 4): remove ranges and insert pauses on a copy of the editor's last timeline.

`transform_otio` is pure (OpenTimelineIO in, OpenTimelineIO out) and is unit tested without Resolve.
`run_pass` is the thin Resolve adapter: export the source, transform, re-import, recolour, report. It refuses to run
when the source is not the highest version or has changed since the plan was made (the editor retouches each version).
"""
import json
import os
import re
import time

from .series import role_of

FPS = 24


# ---- versions ---------------------------------------------------------------------------------------------------
_VER = re.compile(r'^(?P<base>.+?)\s+V(?P<major>\d+)\.(?P<minor>\d+)\b(?P<rest>.*)$')


def parse_version(name):
    m = _VER.match(name or '')
    return (m.group('base'), int(m.group('major')), int(m.group('minor'))) if m else None


def latest_version(names, base):
    """Highest 'Base Vx.n - action' among timeline names (None when the base has no versions)."""
    best = None
    for n in names:
        v = parse_version(n)
        if v and v[0] == base and (best is None or v[1:] > best[0][1:]):
            best = (v, n)
    return best[1] if best else None


def next_version_name(names, base, action):
    last = latest_version(names, base)
    major, minor = (parse_version(last)[1:] if last else (1, -1))
    return f'{base} V{major}.{minor + 1} - {action}'


# ---- edit map ---------------------------------------------------------------------------------------------------
def validate_edits(edits, end):
    """Edits are {P, Q, D, panels?}: remove source frames [P, Q) and insert a pause of D frames there."""
    es = sorted(edits, key=lambda e: e['P'])
    for e in es:
        if not (0 <= e['P'] <= e['Q'] <= end) or e['D'] < 0:
            raise ValueError(f'invalid edit {e}')
    for a, b in zip(es, es[1:]):
        if a['Q'] > b['P']:
            raise ValueError(f'overlapping edits at {a["P"]} / {b["P"]}')
    return es


def build_map(edits, end):
    """-> (kept, new_end, fwd, back). kept = [{a, b, new}] ranges of the old timeline that survive.
    fwd(old) maps an old frame to the new timeline (a removed frame lands after its pause); back(new) is the inverse."""
    es = validate_edits(edits, end)
    kept, pos, prev = [], 0, 0
    for e in es:
        kept.append(dict(a=prev, b=e['P'], new=pos))
        pos += e['P'] - prev
        e['new'] = pos
        pos += e['D']
        prev = e['Q']
    kept.append(dict(a=prev, b=end, new=pos))
    new_end = pos + end - prev

    def fwd(f):
        for k in kept:
            if k['a'] <= f < k['b'] or (f == end and k['b'] == end):
                return k['new'] + f - k['a']
        e = next(e for e in es if e['P'] <= f < e['Q'])
        return e['new'] + e['D']

    def back(n):
        for k in kept:
            if k['new'] <= n < k['new'] + (k['b'] - k['a']):
                return k['a'] + n - k['new']
        return None

    return kept, new_end, fwd, back


def default_is_graphic(track_name, video_index=0):
    return not role_of(track_name).startswith('camera')


def transform_otio(tl, edits, new_name, fps=FPS, is_graphic=default_is_graphic):
    """Apply `edits` to every non-graphic track (cuts and pauses ripple) while graphics follow their content;
    panels named in an edit's `panels` start at the start of its pause. An edit {P: c, Q: c, D: k, extend: True}
    moves the cut at c by k frames instead of inserting a pause: the outgoing clip of every cut track gets k more
    frames of its own source (to land in a silence) and the rest ripples. Returns (new timeline, map dict).
    The source `tl` is never modified."""
    import opentimelineio as otio

    def fr(t):
        v = t.rescaled_to(fps).value
        assert abs(v - round(v)) < 1e-6, f'sub-frame time {v}'
        return int(round(v))

    def gap(n):
        return otio.schema.Gap(duration=otio.opentime.RationalTime(n, fps))

    END = fr(tl.duration())
    edits = [dict(e) for e in edits]
    kept, NEWEND, fwd, back = build_map(edits, END)
    es = sorted(edits, key=lambda e: e['P'])
    panel_of = {name: e for e in es for name in e.get('panels', [])}
    bounds = sorted({x for e in es for x in (e['P'], e['Q'])})

    def piece(item, a, b, cursor):
        pc = item.deepcopy()
        s0 = item.trimmed_range().start_time
        pc.source_range = otio.opentime.TimeRange(s0 + otio.opentime.RationalTime(a - cursor, s0.rate),
                                                  otio.opentime.RationalTime(b - a, s0.rate))
        ps = pc.source_range.start_time
        pe = ps + pc.source_range.duration
        mks = list(pc.markers)
        pc.markers.clear()
        for mk in mks:
            if ps <= mk.marked_range.start_time < pe:
                nm = mk.deepcopy()
                nm.marked_range = otio.opentime.TimeRange(
                    mk.marked_range.start_time, min(mk.marked_range.duration, pe - mk.marked_range.start_time))
                pc.markers.append(nm)
        return pc

    out = tl.deepcopy()
    out.name = new_name
    link_ids, nxt = {}, 1
    video_i = -1
    for orig, target in zip(tl.tracks, out.tracks):
        target.clear()
        if orig.kind == 'Video':
            video_i += 1
        if orig.kind == 'Video' and is_graphic(orig.name, video_i):
            placed, cursor = [], 0
            used = {}
            for item in orig:
                if isinstance(item, otio.schema.Transition):
                    continue
                L = fr(item.duration())
                if isinstance(item, otio.schema.Clip):
                    e = panel_of.get(item.name)
                    if e is not None:
                        off = used.get(id(e), 0)
                        new = e['new'] + off
                        used[id(e)] = off + L
                    else:
                        new = fwd(cursor)
                    placed.append((new, L, item))
                cursor += L
            placed.sort(key=lambda x: x[0])
            c = 0
            for new, L, item in placed:
                if new < c:
                    raise ValueError(f'{orig.name} overlap at {item.name} ({new} < {c})')
                if new > c:
                    target.append(gap(new - c))
                target.append(item.deepcopy())
                c = new + L
            if c < NEWEND:
                target.append(gap(NEWEND - c))
            continue
        pieces, cursor = [], 0
        for item in orig:
            if isinstance(item, otio.schema.Transition):
                raise ValueError(f'transition on {orig.name}: not supported on cut tracks')
            L = fr(item.duration())
            cuts = [cursor] + [x for x in bounds if cursor < x < cursor + L] + [cursor + L]
            for a, b in zip(cuts, cuts[1:]):
                if isinstance(item, otio.schema.Gap):
                    pieces.append((a, b, gap(b - a)))
                    continue
                pc = piece(item, a, b, cursor) if len(cuts) > 2 else item.deepcopy()
                meta = pc.metadata.get('Resolve_OTIO', {})
                if len(cuts) > 2 and meta.get('Link Group ID') is not None:
                    key = (meta['Link Group ID'], a, b)
                    if key not in link_ids:
                        link_ids[key] = 300000 + nxt
                        nxt += 1
                    meta['Link Group ID'] = link_ids[key]
                pieces.append((a, b, pc))
            cursor += L
        for k, rng in enumerate(kept):
            if k > 0 and es[k - 1]['D']:
                prev = target[len(target) - 1] if len(target) else None
                if es[k - 1].get('extend') and isinstance(prev, otio.schema.Clip):
                    # extend the outgoing piece with D more frames of its own source (cut moved to the silence)
                    sr = prev.source_range
                    prev.source_range = otio.opentime.TimeRange(
                        sr.start_time, sr.duration + otio.opentime.RationalTime(es[k - 1]['D'], sr.duration.rate))
                else:
                    target.append(gap(es[k - 1]['D']))
            for a, b, pc in pieces:
                if rng['a'] <= a and b <= rng['b']:
                    target.append(pc)
    lens = {t.name: fr(t.duration()) for t in out.tracks}
    if set(lens.values()) != {NEWEND}:
        raise ValueError(f'track lengths differ after the pass: {lens}')
    out.tracks.markers.clear()
    for mk in tl.tracks.markers:
        nm = mk.deepcopy()
        nm.marked_range = otio.opentime.TimeRange(
            otio.opentime.RationalTime(fwd(fr(mk.marked_range.start_time)), fps), mk.marked_range.duration)
        out.tracks.markers.append(nm)
    return out, dict(kept=kept, edits=es, END=END, NEWEND=NEWEND)


# ---- silence snapping (waveform) --------------------------------------------------------------------------------
def room_tone(after_levels):
    """Quietest levels seen right after cuts: the 'room tone' reference (one low percentile of all probes)."""
    vals = sorted(v for lv in after_levels for v in lv)
    return vals[min(len(after_levels), len(vals) - 1)] if vals else -90.0


def extend_to_silence(tail_db, after_db, room_db, margin_db=8.0, max_k=8):
    """How many frames a cut must be extended to land in silence. 0 = already silent; None = no silence within
    max_k frames (the cut gets a yellow 'Coupe a ecouter' marker instead of being moved)."""
    limit = room_db + margin_db
    if tail_db <= limit:
        return 0
    for k, v in enumerate(after_db[:max_k + 1]):
        if v <= limit:
            return k
    return None


def plan_silence_extensions(snap, values, level_db):
    """Real cuts (the voice and every camera cut on the same frame) whose outgoing audio still has sound are
    extended up to the first silent frame. -> (edits for transform_otio, unresolved cuts [(frame, tail_db)]).
    level_db(file, start_seconds, duration_seconds) -> dB (checks.ffmpeg_level_db in production)."""
    cuts_cfg = values['cuts']
    max_k, margin = cuts_cfg['silence_max_extend_frames'], cuts_cfg['silence_margin_db']
    ka = values['tracks']['pairs'][0][0]
    voice = [c for c in snap.track('audio', ka) if c.has_media and c.start < snap.out_frame]
    cam_cuts = [{c.start for c in snap.track('video', i)} for i in snap.tracks('video')
                if role_of(snap.track_names.get(('video', i), '')).startswith('camera')]
    probes = []
    for a, b in zip(voice, voice[1:]):
        if a.end != b.start or not all(b.start in s for s in cam_cuts):
            continue
        fps = a.media_fps
        end_s = (a.src_start + a.length) / fps
        tail = level_db(a.file, end_s - 2 / fps, 2 / fps)
        after = [level_db(a.file, end_s + j / fps, 1 / fps) for j in range(max_k + 1)]
        probes.append((a.end, tail, after))
    room = room_tone([p[2] for p in probes])
    edits, unresolved = [], []
    for frame, tail, after in probes:
        k = extend_to_silence(tail, after, room, margin, max_k)
        if k is None:
            unresolved.append((frame, round(tail, 1)))
        elif k > 0:
            edits.append(dict(P=frame, Q=frame, D=k, extend=True, text=f'coupe prolongee de {k} images'))
    return edits, unresolved


# ---- Resolve adapters (not unit tested: they need a running Resolve) -------------------------------------------
def media_folders_used(project, timeline):
    used = {x.GetMediaPoolItem().GetUniqueId() for k in ('video', 'audio') for i in range(1, timeline.GetTrackCount(k) + 1)
            for x in (timeline.GetItemListInTrack(k, i) or []) if x.GetMediaPoolItem()}
    folders = []

    def walk(f):
        if any(c.GetUniqueId() in used for c in (f.GetClipList() or [])):
            folders.append(f)
        for s in f.GetSubFolderList() or []:
            walk(s)

    walk(project.GetMediaPool().GetRootFolder())
    return used, folders


def run_pass(resolve, project, plan, workdir):
    """plan: {SRC, NEW, edits, expect_end?, force?, map?}. Creates NEW from SRC. Never edits SRC.
    expect_end = the source length (frames) when the plan was made; a different length means the editor retouched
    the timeline since, so the pass stops (doc 25: always start from the editor's last timeline)."""
    import opentimelineio as otio
    names = [project.GetTimelineByIndex(i).GetName() for i in range(1, project.GetTimelineCount() + 1)]
    ts = {project.GetTimelineByIndex(i).GetName(): project.GetTimelineByIndex(i)
          for i in range(1, project.GetTimelineCount() + 1)}
    SRC, NEW = plan['SRC'], plan['NEW']
    if NEW in ts:
        raise SystemExit(f'{NEW} exists.')
    src = ts[SRC]
    st = src.GetStartFrame()
    base = (parse_version(SRC) or (None,))[0]
    if base and latest_version(names, base) != SRC and not plan.get('force'):
        raise SystemExit(f'{SRC} is not the highest version of "{base}" ({latest_version(names, base)}). '
                         f'Start from the editor\'s last timeline.')
    if plan.get('expect_end') is not None and int(src.GetEndFrame() - st) != int(plan['expect_end']):
        raise SystemExit(f'{SRC} changed since the plan ({src.GetEndFrame() - st} frames, expected {plan["expect_end"]}).')
    os.makedirs(workdir, exist_ok=True)
    path_in = os.path.join(workdir, 'pass_in.otio')
    if not src.Export(path_in, resolve.EXPORT_OTIO):
        raise SystemExit('export failed')
    out, info = transform_otio(otio.adapters.read_from_file(path_in), plan['edits'], NEW)
    path_out = os.path.join(workdir, 'pass_out.otio')
    otio.adapters.write_to_file(out, path_out)
    used, folders = media_folders_used(project, src)
    created = project.GetMediaPool().ImportTimelineFromFile(
        path_out, {'timelineName': NEW, 'importSourceClips': False, 'sourceClipsFolders': folders})
    if not created:
        raise SystemExit('import failed')
    time.sleep(1)
    if created.GetStartTimecode() != src.GetStartTimecode():
        created.SetStartTimecode(src.GetStartTimecode())
    new_used, _ = media_folders_used(project, created)
    if not new_used <= used:
        raise SystemExit('import created new media in the Media Pool; inspect before continuing.')
    _, _, _, back = build_map(plan['edits'], info['END'])
    cst = created.GetStartFrame()
    for i in range(1, created.GetTrackCount('video') + 1):
        srcs = [(int(x.GetStart() - st), int(x.GetEnd() - st), x.GetClipColor(), x.GetClipEnabled())
                for x in src.GetItemListInTrack('video', i) if x.GetMediaPoolItem()]
        role = role_of(created.GetTrackName('video', i))
        for x in created.GetItemListInTrack('video', i):
            if not x.GetMediaPoolItem():
                continue
            if role == 'broll':
                x.SetClipColor('Green')
                continue
            if role == 'graphic':
                x.SetClipColor('Purple')
                continue
            f0 = back(int(x.GetStart() - cst))
            hit = next(((c, en) for sa, sb, c, en in srcs if f0 is not None and sa <= f0 < sb), None)
            if hit:
                if hit[0]:
                    x.SetClipColor(hit[0])
                x.SetClipEnabled(hit[1])
    json.dump(info, open(os.path.join(workdir, plan.get('map', 'pass_map.json')), 'w'), indent=1)
    return dict(name=created.GetName(), frames=int(created.GetEndFrame() - cst), expected=info['NEWEND'],
                delta_s=round((info['NEWEND'] - info['END']) / FPS, 1), edits=len(info['edits']))


def pass_markers(timeline, info, color_removed='Cyan', color_pause='Purple'):
    """Traceability on the new timeline: one marker per added junction (cyan = text removed, violet = pause)."""
    n = 0
    for e in info['edits']:
        label = e.get('text', '')
        if e['D']:
            n += bool(timeline.AddMarker(int(e['new']), color_pause, 'Pause inseree', label or '', int(e['D']), ''))
        elif e['Q'] > e['P']:
            n += bool(timeline.AddMarker(int(e['new']), color_removed, 'Retire', label or f'{e["Q"] - e["P"]} images', 1, ''))
    return n


def new_findings(old_findings, new_findings, back, tol=1):
    """Findings of the timeline AFTER a pass that did not exist before it (a cut in a pause creates a jump cut on the
    same camera, for example). `back(new_frame)` maps a new frame to the old timeline (None inside a pause)."""
    old = {(f['check'], f['frame']) for f in old_findings}
    out = []
    for f in new_findings:
        o = back(f['frame'])
        if o is None or not any((f['check'], o + d) in old for d in range(-tol, tol + 1)):
            out.append(f)
    return out


def mark_findings(timeline, findings, color='Yellow', name='Verifier: cree par la passe'):
    """One marker per finding on the new timeline (frames relative to the timeline start). Returns how many were added."""
    n = 0
    for f in findings:
        n += bool(timeline.AddMarker(int(f['frame']), color, name, f['message'], max(int(f.get('frames', 1)), 1), ''))
    return n


def restore_after_import(project, src, new, fade_frames=12, audio_fade=3, time_sleep=time.sleep):
    """After an OTIO re-import: panel cross-dissolves (same name AND rank as in the source, so clips the editor split
    by hand keep theirs), audio fades at every pause, voice isolation copied from the source."""
    def base(n):
        return re.sub(r'_v\d+(\.\w+)$', r'', n)    # Lili01_v001.mov == Lili01_v002.mov (new render of one panel)

    trans = {}
    for i in range(1, src.GetTrackCount('video') + 1):
        if role_of(src.GetTrackName('video', i)) != 'graphic':
            continue
        its = src.GetItemListInTrack('video', i)
        starts = {int(x.GetStart()) for x in its if not x.GetMediaPoolItem()}
        seen, trans[i] = {}, set()
        for x in its:
            if not x.GetMediaPoolItem():
                continue
            k = (base(x.GetName()), seen.get(base(x.GetName()), 0))
            seen[base(x.GetName())] = k[1] + 1
            if int(x.GetStart()) in starts:
                trans[i].add(k)
    project.SetCurrentTimeline(new)
    time_sleep(2)
    fades = 0
    for i, names in trans.items():
        seen = {}
        for x in new.GetItemListInTrack('video', i):
            if not x.GetMediaPoolItem():
                continue
            k = (base(x.GetName()), seen.get(base(x.GetName()), 0))
            seen[base(x.GetName())] = k[1] + 1
            if k in names:
                fades += bool(x.AddTransition({'type': 'Cross Dissolve', 'category': 'simple', 'position': 'start',
                                               'alignment': 'right', 'duration': fade_frames}))
    af = 0
    for i in (1, 2):
        its = new.GetItemListInTrack('audio', i)
        for a, b in zip(its, its[1:]):
            if b.GetStart() > a.GetEnd():
                fa, fb = a.GetFades() or {}, b.GetFades() or {}
                a.SetFades({'FadeIn': fa.get('FadeIn', 0), 'FadeOut': audio_fade})
                b.SetFades({'FadeIn': audio_fade, 'FadeOut': fb.get('FadeOut', 0)})
                af += 1
    project.SetCurrentTimeline(src)
    time_sleep(1)
    iso = src.GetVoiceIsolationState(1)
    project.SetCurrentTimeline(new)
    time_sleep(1)
    new.SetVoiceIsolationState(1, {'isEnabled': iso['isEnabled'], 'amount': iso['amount']})
    return dict(transitions_src=sum(len(v) for v in trans.values()), transitions_added=fades, audio_fade_pairs=af,
                isolation=new.GetVoiceIsolationState(1))

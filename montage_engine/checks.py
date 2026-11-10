"""Read-only checks run after every pass (doc 25, section 2C). Pure functions over a Snapshot.

Every check returns a list of findings: dict(check, frame, frames, message, severity). `run_all` merges them in one
report. Thresholds come from the resolved directives (series.resolve_directives), never from hard-coded numbers.
"""
import array
import math
import statistics
import subprocess

from .model import tc
from .series import overlay_params, role_of


def finding(check, frame, frames, message, severity='error'):
    return dict(check=check, frame=int(frame), frames=int(frames), message=message, severity=severity)


# ---- camera helpers ---------------------------------------------------------------------------------------------
def camera_tracks(snap):
    """(bottom, top) camera video tracks by NAME role; the higher track hides the lower when enabled."""
    cams = sorted(i for (k, i), n in snap.track_names.items() if k == 'video' and role_of(n).startswith('camera'))
    if len(cams) >= 2:
        return cams[0], cams[-1]
    if len(cams) == 1:
        return cams[0], None
    return None, None


def visible(snap, f, bottom, top):
    """(camera track, clip) the viewer sees at frame f, ignoring overlays."""
    if top is not None:
        x = snap.at('video', top, f)
        if x is not None and x.enabled:
            return top, x
    if bottom is not None:
        x = snap.at('video', bottom, f)
        if x is not None:
            return bottom, x
    return None, None


def shots(snap, start=0, end=None):
    """Visible shots: runs of the same camera continuing the same take. [dict(start,end,cam,file,src)]."""
    bottom, top = camera_tracks(snap)
    end = snap.out_frame if end is None else end
    cuts = {start, end}
    for t in (bottom, top):
        for c in snap.track('video', t) if t else []:
            for f in (c.start, c.end):
                if start <= f <= end:
                    cuts.add(f)
    bounds = sorted(cuts)
    out = []
    for a, b in zip(bounds, bounds[1:]):
        cam, clip = visible(snap, a, bottom, top)
        if clip is None:
            continue
        if out and out[-1]['cam'] == cam and out[-1]['end'] == a and out[-1]['file'] == clip.file \
                and out[-1]['src_end'] == clip.src_at(a):
            out[-1]['end'] = b
            out[-1]['src_end'] = clip.src_at(b)
        else:
            out.append(dict(start=a, end=b, cam=cam, file=clip.file, src_end=clip.src_at(b)))
    return out


def cover_and_fades(snap, values):
    """Frames where graphics/b-roll hide the cameras (cover) and where their fades run (fades)."""
    cover, fades = [], []
    for (kind, i), name in snap.track_names.items():
        if kind != 'video':
            continue
        role = role_of(name)
        if role not in ('graphic', 'broll'):
            continue
        for x in snap.track('video', i):
            if not x.has_media:
                continue
            p = overlay_params(values, x.name)
            if role == 'broll':
                cover.append((x.start, x.end))
                fades.append((x.start - p['pad_frames'], x.start + p['pad_frames']))
                fades.append((x.end - p['pad_frames'], x.end + p['pad_frames']))
            else:
                fi, fo, pad = p['fade_in_frames'], p['fade_out_frames'], p['pad_frames']
                cover.append((x.start + fi, x.end - fo))
                fades.append((x.start - pad, x.start + fi))
                fades.append((x.end - fo, x.end + pad))
    return cover, fades


def _within(ranges, f):
    return any(a <= f < b for a, b in ranges)


# ---- the checks -------------------------------------------------------------------------------------------------
def check_sync(snap, values):
    """Image = sound: under every voice clip the camera picture comes from the same take at the same frame."""
    tol = values['sync']['tolerance_frames']
    found = []
    for ka, kv in values['tracks']['pairs']:
        A = [c for c in snap.track('audio', ka) if c.has_media]
        V = [c for c in snap.track('video', kv) if c.has_media]
        for a in A:
            if a.start >= snap.out_frame:
                break
            covered = []
            for v in V:
                lo, hi = max(a.start, v.start), min(a.end, v.end)
                if lo >= hi:
                    continue
                covered.append((lo, hi))
                if a.file != v.file or abs(a.src_at(lo) - v.src_at(lo)) > tol:
                    found.append(finding('sync', lo, hi - lo,
                                         f'A{ka}/V{kv}: picture {v.file[-12:]}@{v.src_at(lo)} under sound '
                                         f'{a.file[-12:]}@{a.src_at(lo)}'))
            cur = a.start
            for lo, hi in sorted(covered):
                if lo > cur:
                    found.append(finding('sync', cur, lo - cur, f'V{kv} empty under A{ka}'))
                cur = max(cur, hi)
            if cur < a.end:
                found.append(finding('sync', cur, a.end - cur, f'V{kv} empty under A{ka}'))
    return found


def check_min_shot(snap, values):
    mn = values['cameras']['min_shot_seconds']
    if not mn:
        return []
    need = int(round(mn * snap.fps))
    cover, _ = cover_and_fades(snap, values)
    found = []
    for s in shots(snap):
        if s['end'] - s['start'] < need and not _within(cover, s['start']):
            found.append(finding('min_shot', s['start'], s['end'] - s['start'],
                                 f'visible shot of {(s["end"] - s["start"]) / snap.fps:.2f}s on V{s["cam"]} '
                                 f'(minimum {mn}s)'))
    return found


def check_max_same_camera(snap, values):
    mx = values['cameras']['max_same_camera_seconds']
    if not mx:
        return []
    limit = int(round(mx * snap.fps))
    runs, run = [], None
    for s in shots(snap):
        if run and run['cam'] == s['cam'] and run['end'] == s['start']:
            run['end'] = s['end']
        else:
            run = dict(start=s['start'], end=s['end'], cam=s['cam'])
            runs.append(run)
    cover, _ = cover_and_fades(snap, values)
    found = []
    for r in runs:
        # only the part the viewer really sees counts: b-roll and panels hide the camera (doc 25)
        edges = sorted({r['start'], r['end']} | {x for a, b in cover for x in (a, b) if r['start'] < x < r['end']})
        longest = max((b - a for a, b in zip(edges, edges[1:]) if not _within(cover, a)), default=0)
        if longest > limit:
            found.append(finding('max_same_camera', r['start'], r['end'] - r['start'],
                                 f'{longest / snap.fps:.1f}s visible on the same camera V{r["cam"]} (maximum {mx}s)',
                                 'warning'))
    return found


def camera_share(snap):
    """{camera track: share of the In-Out range}"""
    total = {}
    for s in shots(snap):
        total[s['cam']] = total.get(s['cam'], 0) + (s['end'] - s['start'])
    whole = sum(total.values()) or 1
    return {k: v / whole for k, v in total.items()}


def check_camera_share(snap, values):
    target = values['cameras']['main_share']
    if target is None:
        return []
    bottom, top = camera_tracks(snap)
    main = next((i for (k, i), n in snap.track_names.items() if k == 'video' and role_of(n) == 'camera_main'), None)
    share = camera_share(snap).get(main, 0.0)
    if abs(share - target) > values['cameras']['share_tolerance']:
        return [finding('camera_share', 0, snap.out_frame,
                        f'main camera V{main} shown {share:.0%} of the time (target {target:.0%})', 'warning')]
    return []


def check_same_camera_jump_cuts(snap, values, start=0):
    """Visible cuts where the same camera jumps in the source and nothing hides it (jump cuts)."""
    bottom, top = camera_tracks(snap)
    cover, _ = cover_and_fades(snap, values)
    found = []
    for s0, s1 in zip(shots(snap, start), shots(snap, start)[1:]):
        if s0['end'] != s1['start'] or s0['cam'] != s1['cam']:
            continue
        c = s1['start']
        if _within(cover, c) or _within(cover, c - 1):
            continue
        found.append(finding('same_camera_jump_cut', c, 1, f'jump cut on the same camera V{s0["cam"]}'))
    return found


def check_cut_in_fade_zone(snap, values):
    """A camera change or cut inside the fade of an overlay (three simultaneous transitions)."""
    _, fades = cover_and_fades(snap, values)
    found = []
    for s in shots(snap)[1:]:
        if any(a < s['start'] < b for a, b in fades):
            found.append(finding('cut_in_fade', s['start'], 1, 'camera change or cut during an overlay fade'))
    return found


def check_broll_gaps(snap, values):
    small = values['broll'].get('small_gap_frames', 12)
    found = []
    for (kind, i), n in snap.track_names.items():
        if kind == 'video' and role_of(n) == 'broll':
            cl = [c for c in snap.track('video', i) if c.has_media]
            for a, b in zip(cl, cl[1:]):
                if 0 < b.start - a.end <= small:
                    found.append(finding('broll_gap', a.end, b.start - a.end,
                                         f'{b.start - a.end}-frame gap between two b-roll clips'))
    return found


def check_remnants(snap, values):
    """Tiny audio pieces (<= N frames) that continue neither neighbour."""
    mx = values['cuts']['remnant_max_frames']
    found = []
    for ka, _ in values['tracks']['pairs'][:1]:
        A = [c for c in snap.track('audio', ka) if c.has_media]
        for k, c in enumerate(A):
            if c.length > mx or c.start >= snap.out_frame:
                continue
            pv = A[k - 1] if k else None
            nx = A[k + 1] if k + 1 < len(A) else None
            if (pv and pv.continues(c)) or (nx and c.continues(nx)):
                continue
            found.append(finding('remnant', c.start, c.length, f'{c.length}-frame audio piece joined to nothing'))
    return found


def ffmpeg_level_db(path, start_s, dur_s):
    """RMS level in dB of a window of a media file (16 kHz mono through ffmpeg)."""
    r = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{max(start_s, 0):.5f}', '-t', f'{dur_s:.5f}', '-i', path,
                        '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], capture_output=True)
    a = array.array('h')
    a.frombytes(r.stdout[:len(r.stdout) // 2 * 2])
    x = [v / 32768 for v in a]
    return 20 * math.log10(math.sqrt(sum(v * v for v in x) / max(len(x), 1)) + 1e-9)


def check_cut_on_sound(snap, values, level_db=ffmpeg_level_db):
    """Waveform check: an audio cut is suspect when the frames on either side are loud (likely inside a word)."""
    margin = values['cuts']['wave_suspect_margin_db']
    probe = values['cuts']['wave_probe_frames']
    ka = values['tracks']['pairs'][0][0]
    A = [c for c in snap.track('audio', ka) if c.has_media and c.start < snap.out_frame]
    rows, ctx_levels = [], []
    for a, b in zip(A, A[1:]):
        if a.end != b.start:
            continue                                          # a pause gap, not a cut inside speech
        fa, fb = a.media_fps, b.media_fps
        end_a = (a.src_start + a.length) / fa
        out_db = level_db(a.file, end_a - probe / fa, probe / fa)
        in_db = level_db(b.file, b.src_start / fb, probe / fb)
        ctx = level_db(a.file, max(end_a - 3, 0), min(3, end_a))
        ctx_levels.append(ctx)
        rows.append((b.start, out_db, in_db))
    if not rows:
        return []
    ref = statistics.median(ctx_levels)
    return [finding('cut_on_sound', f, 1, f'cut on sound: out {o:.0f} dB, in {i:.0f} dB (speech {ref:.0f} dB)',
                    'warning') for f, o, i in rows if max(o, i) > ref - margin]


def compare_to_editor(snap, editor):
    """The new timeline must be as long as the editor's last one, unless this pass is meant to change it."""
    new_end = max((c.end for c in snap.clips), default=0)
    old_end = max((c.end for c in editor.clips), default=0)
    if new_end != old_end:
        return [finding('vs_editor', 0, abs(new_end - old_end),
                        f'length {new_end} frames vs the editor\'s {old_end} ({new_end - old_end:+d})', 'info')]
    return []


CHECKS = [check_sync, check_min_shot, check_max_same_camera, check_camera_share, check_same_camera_jump_cuts,
          check_cut_in_fade_zone, check_broll_gaps, check_remnants]


def run_all(snap, values, with_waveform=False, level_db=ffmpeg_level_db, editor=None):
    found = []
    for chk in CHECKS:
        found += chk(snap, values)
    if with_waveform:
        found += check_cut_on_sound(snap, values, level_db)
    if editor is not None:
        found += compare_to_editor(snap, editor)
    found.sort(key=lambda x: (x['frame'], x['check']))
    counts = {}
    for f in found:
        counts[f['check']] = counts.get(f['check'], 0) + 1
    errors = [f for f in found if f['severity'] == 'error']
    return dict(timeline=snap.name, ok=not errors, errors=len(errors), counts=counts,
                camera_share=camera_share(snap), findings=found,
                lines=[f'{tc(f["frame"], snap.fps)} {f["frames"]:>4}im. {f["check"]}: {f["message"]}' for f in found])

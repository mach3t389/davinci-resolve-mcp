"""Panels / overlays (doc 25, step 7): kinds, measured transparency, and chaining of close panels.

A panel's kind comes from what is under it, not from its name:
  chapter  - no voice under it (a pause: the read title is removed and replaced by the panel)
  overlay  - the voice goes on under it (quotes, visuals)
Its COVERAGE comes from its render: 'fullscreen' (a transition integrated in a full-screen motion design) or
'partial' (an overlay over the picture, which may sit over several shots). Only a full-screen panel constrains the
cuts around its fades; a partial overlay lets the shots change under it (Alexis, 10 Nov 2026).
Fades are READ, not computed: the Motion engine already knows them and writes a sidecar next to the render
(`<render>.mov.motion.json`, see `SIDECAR_FIELDS`). The alpha of the file is measured with ffmpeg only as a fallback for
renders without a sidecar (stock, third-party visuals).
Chaining (Masterclass rule): when two panels are less than `chain_gap_frames` apart, the second goes on the track
above and must be fully visible when the first starts to fade out.
"""
import json
import os
import re
import subprocess

SIDECAR_SUFFIX = '.motion.json'
SIDECAR_FIELDS = ('n', 'in_full', 'out_start', 'coverage')   # + optional first_visible, last_visible
FULLSCREEN_ALPHA = 0.9    # mean alpha of the opaque frames above which a render covers the whole picture


def profile_from_alpha(values):
    """values: mean alpha per frame of a render (0..255 or 0..1). -> dict(n, in_full, out_start, first_visible,
    last_visible) in FILE frames: first fully visible frame, frame where the fade-out starts, first/last visible."""
    if not values:
        return None
    mx = max(values)
    if mx <= 0:
        return None
    full = [k for k, v in enumerate(values) if v >= 0.98 * mx]
    vis = [k for k, v in enumerate(values) if v > 0.02 * mx]
    scale = 255.0 if mx > 1.5 else 1.0
    return dict(n=len(values), in_full=full[0], out_start=full[-1] + 1, first_visible=vis[0], last_visible=vis[-1],
                coverage='fullscreen' if mx / scale >= FULLSCREEN_ALPHA else 'partial', source='measured')


def sidecar_path(render_path):
    return str(render_path) + SIDECAR_SUFFIX


def read_sidecar(render_path):
    """Fades written by the Motion render step: {n, in_full, out_start, coverage} or None. A sidecar missing a field
    or holding a wrong type is ignored (the caller then falls back to measuring), never half-trusted."""
    p = sidecar_path(render_path)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or any(k not in data for k in SIDECAR_FIELDS):
        return None
    if data['coverage'] not in ('fullscreen', 'partial') or not all(isinstance(data[k], int) for k in ('n', 'in_full', 'out_start')):
        return None
    if not 0 <= data['in_full'] <= data['out_start'] <= data['n']:
        return None
    return dict(data, source='sidecar')


def write_sidecar(render_path, n, in_full, out_start, coverage):
    """What the Motion render step writes next to `<name>_v001.mov`, from the animation's own fade parameters."""
    data = dict(n=int(n), in_full=int(in_full), out_start=int(out_start), coverage=coverage)
    if coverage not in ('fullscreen', 'partial') or not 0 <= data['in_full'] <= data['out_start'] <= data['n']:
        raise ValueError(f'invalid fade information {data}')
    with open(sidecar_path(render_path), 'w', encoding='utf8') as f:
        json.dump(data, f)
    return data


def profile_for(render_path, measure=True):
    """Fade profile of a render: the sidecar first (information already transmitted), the ffmpeg measurement only
    when there is none and `measure` is true. None when unknown."""
    return read_sidecar(render_path) or (alpha_profile_file(render_path) if measure else None)


def alpha_profile_file(path):
    """Measure the real alpha of a render with ffmpeg (None when the file has no alpha channel).
    `format=gray` is required: on a 10-bit render (Remotion's yuva444p10le) the alpha otherwise comes out on a larger
    scale (3760 for an opaque frame) and every render would look full screen."""
    res = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-vf',
                          'alphaextract,format=gray,scale=160:90,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-',
                          '-f', 'null', '-'], capture_output=True, text=True)
    return profile_from_alpha([float(v) for v in re.findall(r'YAVG=([\d.]+)', res.stdout)])


def profiles_for_snapshot(snap, role_of_track, measure=False):
    """{render file path: fade profile} for every graphic clip of a snapshot. Sidecars are read for free; the ffmpeg
    measurement (slow, a pass over the whole file) runs only with measure=True and only for renders without one."""
    out = {}
    for (kind, i), name in snap.track_names.items():
        if kind == 'video' and role_of_track(name, 'video') in ('graphic', 'broll'):
            for c in snap.track('video', i):
                if c.has_media and c.file and c.file not in out:
                    prof = profile_for(c.file, measure=measure)
                    if prof:
                        out[c.file] = prof
    return out


def base_name(name):
    """Lili01_v002.mov -> Lili01: a new render of the same panel keeps its identity."""
    return re.sub(r'_v\d+(\.\w+)$', '', name)


def panel_kind(snap, clip, voice_tracks=(1, 2), min_overlap=0.5):
    """'overlay' when the voice continues under at least `min_overlap` of the panel, else 'chapter'."""
    covered = 0
    for v in snap.clips:
        if v.kind == 'audio' and v.track in voice_tracks and v.has_media:
            lo, hi = max(v.start, clip.start), min(v.end, clip.end)
            covered += max(0, hi - lo)
    return 'overlay' if covered >= min_overlap * max(clip.length, 1) else 'chapter'


def panels(snap, roles):
    """Graphic clips with media, in timeline order: [(clip, kind)]."""
    out = []
    for (kind, i), role in roles.items():
        if kind == 'video' and role == 'graphic':
            for c in snap.track('video', i):
                if c.has_media:
                    out.append((c, panel_kind(snap, c)))
    return sorted(out, key=lambda x: x[0].start)


def chain_start(first_start, first_profile, second_profile, crossfade_frames=12):
    """Earliest-safe start of the second panel so that it is fully visible when the first begins to fade.
    The second is fully visible `max(in_full, crossfade)` frames after its start."""
    visible_after = max(second_profile['in_full'], crossfade_frames)
    return first_start + first_profile['out_start'] - visible_after


def plan_chains(items, profiles, chain_gap_frames=24, crossfade_frames=12):
    """items: [(clip, kind)] sorted. profiles: {base name: profile}. -> list of
    dict(first, second, gap, new_start, shift, track_above) for consecutive panels closer than chain_gap_frames.
    `shift` < 0 means the second panel moves earlier. Panels are never moved when `kind` is 'overlay' (visuals are
    timed on the speech) and never before the first one starts."""
    plans = []
    for (a, ka), (b, kb) in zip(items, items[1:]):
        gap = b.start - a.end
        if gap >= chain_gap_frames or kb == 'overlay':
            continue
        pa = profiles.get(a.file) or profiles.get(base_name(a.name))
        pb = profiles.get(b.file) or profiles.get(base_name(b.name))
        if not pa or not pb:
            plans.append(dict(first=a.name, second=b.name, gap=gap, error='no alpha profile (no alpha channel?)'))
            continue
        new = max(chain_start(a.start, pa, pb, crossfade_frames), a.start)
        plans.append(dict(first=a.name, second=b.name, gap=gap, new_start=new, shift=new - b.start,
                          track_above=b.track <= a.track))
    return plans

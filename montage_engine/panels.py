"""Panels / overlays (doc 25, step 7): kinds, measured transparency, and chaining of close panels.

A panel's kind comes from what is under it, not from its name:
  chapter  - no voice under it (a pause: the read title is removed and replaced by the panel)
  overlay  - the voice goes on under it (quotes, visuals)
Chaining (Masterclass rule, from the measured alpha of the render): when two panels are less than `chain_gap_frames`
apart, the second goes on the track above and must be fully visible when the first starts to fade out.
"""
import re
import subprocess


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
    return dict(n=len(values), in_full=full[0], out_start=full[-1] + 1, first_visible=vis[0], last_visible=vis[-1])


def alpha_profile_file(path):
    """Measure the real alpha of a render with ffmpeg (None when the file has no alpha channel)."""
    res = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-vf',
                          'alphaextract,scale=160:90,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-',
                          '-f', 'null', '-'], capture_output=True, text=True)
    return profile_from_alpha([float(v) for v in re.findall(r'YAVG=([\d.]+)', res.stdout)])


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
        pa, pb = profiles.get(base_name(a.name)), profiles.get(base_name(b.name))
        if not pa or not pb:
            plans.append(dict(first=a.name, second=b.name, gap=gap, error='no alpha profile (no alpha channel?)'))
            continue
        new = max(chain_start(a.start, pa, pb, crossfade_frames), a.start)
        plans.append(dict(first=a.name, second=b.name, gap=gap, new_start=new, shift=new - b.start,
                          track_above=b.track <= a.track))
    return plans

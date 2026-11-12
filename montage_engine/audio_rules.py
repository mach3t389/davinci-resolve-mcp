"""Sound rules of the montage directives (asked by Alexis on 12 November 2026, Rushflow doc 25).

  cuts.keep_pause_seconds  -> plan_pause_trims: a pause between two sentences longer than this is shortened to it
                              (edits for passes.transform_otio, so they ripple on every cut track like any pass).
  cuts.audio_lead_frames   -> apply_audio_lead: the next sentence is heard this many frames before the speaker is
                              seen, where an overlay hides the cameras (J cut). Sound and camera move together
                              (picture = sound). Pure OpenTimelineIO, the source is never modified.
  audio.target_lufs        -> normalize_loudness: Resolve's own loudness normalization (Timeline.NormalizeAudioLevel)
                              of the voice clips. Resolve adapter, needs a running Resolve.
  audio.music_under_voice_db -> not applied: the scripting API has no audio volume automation (keyframes), so the music
                              cannot be lowered only while someone speaks. The user's assistant follows that rule.

A rule set to None (or missing) is "no rule": nothing is planned or changed for it.
"""
from .anchors import silent_ranges

FPS = 24


def _rule(values, section, key):
    return (values.get(section) or {}).get(key)


# ---- pauses kept ------------------------------------------------------------------------------------------------
def plan_pause_trims(words, values, fps=FPS, min_remove_frames=2, camera_runs=None):
    """Edits {P, Q, D: 0} that shorten every pause between two words to `cuts.keep_pause_seconds`.
    Only pauses BETWEEN words count (never before the first or after the last one); the pause keeps its first
    frames (the end of the sentence breathes), the rest is removed. -> [] when the rule is not set.
    With `camera_runs` (visible camera runs, see camera_runs()), a pause is shortened only where the junction lands
    on a camera change and both shots around it keep `cameras.min_shot_seconds`: shortening a pause inside one shot
    would make a jump cut (first real run, 12 Nov 2026: 111 jump cuts and 158 short shots)."""
    keep_s = _rule(values, 'cuts', 'keep_pause_seconds')
    if keep_s is None or not words:
        return []
    keep = int(round(keep_s * fps))
    min_shot = _rule(values, 'cameras', 'min_shot_seconds')
    need = int(round(min_shot * fps)) if min_shot else 1
    edits = []
    for a, b in silent_ranges(words, min_frames=keep + min_remove_frames, start=words[0]['f0']):
        P, Q = a + keep, b
        if Q - P < min_remove_frames:
            continue
        if camera_runs is not None:
            before = next((r for r in camera_runs if r['start'] < P <= r['end']), None)
            after = next((r for r in camera_runs if r['start'] <= Q < r['end']), None)
            if before is None or after is None or before['cam'] == after['cam'] \
                    or P - before['start'] < need or after['end'] - Q < need:
                continue
        edits.append(dict(P=P, Q=Q, D=0, text=f'pause raccourcie a {keep_s:g} s'))
    return edits


def camera_runs(snapshot):
    """Runs of frames where the same camera is visible (overlays ignored): [{start, end, cam}]."""
    from .checks import shots
    runs = []
    for sh in shots(snapshot):
        if runs and runs[-1]['cam'] == sh['cam'] and runs[-1]['end'] == sh['start']:
            runs[-1]['end'] = sh['end']
        else:
            runs.append(dict(start=sh['start'], end=sh['end'], cam=sh['cam']))
    return runs


def outside_overlays(edits, snapshot, is_camera):
    """Drop the pause trims that fall under a clip of a non-camera video track (panel, animation, b-roll): those
    tracks follow their content in a pass, so shortening a pause under them would make them overlap."""
    covered = [(c.start, c.end) for c in snapshot.clips if c.kind == 'video' and c.has_media and not is_camera(c.track_name)]
    return [e for e in edits if not any(e['P'] < b and a < e['Q'] for a, b in covered)]


# ---- sound ahead of the picture (J cut) -------------------------------------------------------------------------
def apply_audio_lead(tl, values, fps=FPS, is_camera=None):
    """Return (new timeline, {moved, skipped}) where the next sentence is heard `cuts.audio_lead_frames` frames before
    the speaker is seen. Picture = sound stays true: at a real cut (every paired sound and camera track cut on the same
    frame), the cut of ALL those tracks moves earlier together, and only where an overlay (b-roll, panel) hides the
    cameras during those frames. The viewer hears the new sentence over the overlay, then sees the speaker.
    (First real run, 12 Nov 2026: moving the sound alone put 308 picture/sound offsets under the same speaker.)
    A cut without handles in every track, or not hidden by an overlay, is left as it is. `tl` is never modified."""
    import opentimelineio as otio
    from .series import role_of

    is_camera = is_camera or (lambda name: role_of(name).startswith('camera'))
    lead = _rule(values, 'cuts', 'audio_lead_frames')
    out = tl.deepcopy()
    if not lead:
        return out, dict(moved=[], skipped=[])
    audio = [t for t in out.tracks if t.kind == 'Audio']
    video = [t for t in out.tracks if t.kind == 'Video']
    paired = []
    for ka, kv in values['tracks']['pairs']:
        if ka <= len(audio):
            paired.append(audio[ka - 1])
        if kv <= len(video) and is_camera(video[kv - 1].name):
            paired.append(video[kv - 1])
    if not paired:
        return out, dict(moved=[], skipped=[])

    def fr(t):
        return int(round(t.rescaled_to(fps).value))

    def items_at(track):
        """{cut frame: (outgoing clip, incoming clip)} for every cut between two clips of the track."""
        cuts, pos, prev = {}, 0, None
        for item in track:
            if isinstance(item, otio.schema.Transition):
                prev = None
                continue
            if prev is not None and isinstance(prev, otio.schema.Clip) and isinstance(item, otio.schema.Clip):
                cuts[pos] = (prev, item)
            pos += fr(item.duration())
            prev = item
        return cuts

    covered = []
    for t in video:
        if is_camera(t.name):
            continue
        pos = 0
        for item in t:
            if isinstance(item, otio.schema.Transition):
                continue
            n = fr(item.duration())
            if isinstance(item, otio.schema.Clip) and item.enabled:
                covered.append((pos, pos + n))
            pos += n

    def hidden(a, b):
        return any(lo <= a and b <= hi for lo, hi in covered)

    per_track = [items_at(t) for t in paired]
    voice_cuts = sorted(per_track[0])
    moved, skipped = [], []
    for pos in voice_cuts:
        if not all(pos in cuts for cuts in per_track):
            continue                              # not a real cut: the cameras do not cut with the voice here
        if not hidden(pos - lead, pos):
            skipped.append(pos)
            continue
        ok = True
        for cuts in per_track:
            a, b = cuts[pos]
            rate = b.source_range.start_time.rate
            head = b.source_range.start_time
            try:
                head = b.source_range.start_time - b.available_range().start_time
            except Exception:
                pass
            if head.rescaled_to(rate).value < lead or a.source_range.duration.rescaled_to(rate).value <= lead + 1:
                ok = False
        if not ok:
            skipped.append(pos)
            continue
        for cuts in per_track:
            a, b = cuts[pos]
            sa, sb = a.source_range, b.source_range
            n = otio.opentime.RationalTime(lead, sb.start_time.rate)
            a.source_range = otio.opentime.TimeRange(sa.start_time, sa.duration - n)
            b.source_range = otio.opentime.TimeRange(sb.start_time - n, sb.duration + n)
        moved.append(pos)
    return out, dict(moved=moved, skipped=skipped)


# ---- loudness ---------------------------------------------------------------------------------------------------
def pick_loudness_mode(modes):
    """The normalization mode that targets integrated loudness (ITU BS.1770 / LUFS) among Resolve's modes."""
    for wanted in ('bs.1770', 'lufs', 'loudness'):
        for m in modes or []:
            if wanted in m.lower():
                return m
    return None


def normalize_loudness(resolve, timeline, values, audio_tracks=None):
    """Normalize the voice clips to `audio.target_lufs` with Resolve's NormalizeAudioLevel. -> report dict.
    Changes clip gain only on the timeline given (always a copy made by a pass, never the editor's timeline)."""
    target = _rule(values, 'audio', 'target_lufs')
    if target is None:
        return dict(applied=False, reason='no rule')
    tracks = audio_tracks or [values['tracks']['pairs'][0][0]]
    items = [x for i in tracks for x in (timeline.GetItemListInTrack('audio', i) or [])]
    if not items:
        return dict(applied=False, reason='no voice clip')
    mode = pick_loudness_mode(timeline.GetNormalizeAudioModes())
    if mode is None:
        return dict(applied=False, reason='no loudness mode in this Resolve version')
    ok = timeline.NormalizeAudioLevel(items, {
        'normalizationMode': mode, 'targetLoudness': float(target),
        'setLevelMode': resolve.NORMALIZE_AUDIO_SET_LEVEL_RELATIVE,   # keeps the balance between clips
    })
    return dict(applied=bool(ok), mode=mode, target=target, clips=len(items))

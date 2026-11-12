"""Sound rules of the montage directives (asked by Alexis on 12 November 2026, Rushflow doc 25).

  cuts.keep_pause_seconds  -> plan_pause_trims: a pause between two sentences longer than this is shortened to it
                              (edits for passes.transform_otio, so they ripple on every cut track like any pass).
  cuts.audio_lead_frames   -> apply_audio_lead: at each cut of the voice track, the incoming sound starts this many
                              frames before its picture (J cut). Pure OpenTimelineIO, the source is never modified.
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
def plan_pause_trims(words, values, fps=FPS, min_remove_frames=2):
    """Edits {P, Q, D: 0} that shorten every pause between two words to `cuts.keep_pause_seconds`.
    Only pauses BETWEEN words count (never before the first or after the last one); the pause keeps its first
    frames (the end of the sentence breathes), the rest is removed. -> [] when the rule is not set."""
    keep_s = _rule(values, 'cuts', 'keep_pause_seconds')
    if keep_s is None or not words:
        return []
    keep = int(round(keep_s * fps))
    edits = []
    for a, b in silent_ranges(words, min_frames=keep + min_remove_frames, start=words[0]['f0']):
        if b - (a + keep) >= min_remove_frames:
            edits.append(dict(P=a + keep, Q=b, D=0, text=f'pause raccourcie a {keep_s:g} s'))
    return edits


def outside_overlays(edits, snapshot, is_camera):
    """Drop the pause trims that fall under a clip of a non-camera video track (panel, animation, b-roll): those
    tracks follow their content in a pass, so shortening a pause under them would make them overlap."""
    covered = [(c.start, c.end) for c in snapshot.clips if c.kind == 'video' and c.has_media and not is_camera(c.track_name)]
    return [e for e in edits if not any(e['P'] < b and a < e['Q'] for a, b in covered)]


# ---- sound ahead of the picture (J cut) -------------------------------------------------------------------------
def apply_audio_lead(tl, values, fps=FPS):
    """Return (new timeline, moved cuts) where, on the voice track (`tracks.pairs[0][0]`), every cut between two
    clips moves `cuts.audio_lead_frames` frames earlier: the outgoing sound is shortened, the incoming one starts
    earlier in its own source. Pictures and the other tracks do not move; the track length does not change.
    A cut is left as it is (and reported in `skipped`) when the incoming clip has no handle before its start or the
    outgoing clip would become too short. `tl` is never modified."""
    import opentimelineio as otio

    lead = _rule(values, 'cuts', 'audio_lead_frames')
    out = tl.deepcopy()
    if not lead:
        return out, dict(moved=[], skipped=[])
    voice_index = values['tracks']['pairs'][0][0]
    audio = [t for t in out.tracks if t.kind == 'Audio']
    if len(audio) < voice_index:
        return out, dict(moved=[], skipped=[])
    track = audio[voice_index - 1]
    items = list(track)
    moved, skipped, pos = [], [], 0
    for a, b in zip(items, items[1:]):
        pos += int(round(a.duration().rescaled_to(fps).value))
        if not (isinstance(a, otio.schema.Clip) and isinstance(b, otio.schema.Clip)):
            continue
        sa, sb = a.source_range, b.source_range
        rate = sb.start_time.rate
        n = otio.opentime.RationalTime(lead, rate)
        head = sb.start_time
        try:
            head = sb.start_time - b.available_range().start_time
        except Exception:
            pass                                   # unknown media extent: rely on the source start alone
        if head.rescaled_to(rate).value < lead or sa.duration.rescaled_to(rate).value <= lead + 1:
            skipped.append(pos)
            continue
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

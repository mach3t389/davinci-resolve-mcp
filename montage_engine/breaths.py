"""Breaths left at voice cuts (Alexis, 12 Nov 2026: after « eux » in Lili V4.1, the cut kept a breath).

A breath at a cut is a mistake: the cut should land just after the voice. The transcript cannot tell: WhisperX stretches
the last word of a sentence over the breath (« eux » ended 6 frames after the cut while the voice stopped 18 frames
before it). The waveform decides, frame by frame, near every cut of the voice track (end of a clip, and start of a
clip, including next to a pause):
  speech  = frames at least SPEECH_DB over the quietest frame of the window,
  silence = frames under SILENCE_DB over it,
  breath  = a soft bump (between the two) that comes back AFTER a real silence, before the cut.
`find_breaths` reports them, as places TO LISTEN TO only.

NOT RELIABLE ENOUGH TO CUT (real run, 12 Nov 2026): on Lili's camera-2 sound a softly spoken word ("eux", "mais",
"qui suit aussi") measures almost like the silence, so 8 of the 18 pieces removed on a copy were real words, and the
cuts also broke the picture of a finished edit. The automatic correction was removed from the command line; never cut
from this alone. A candidate that contains the START of a transcribed word is dropped (word starts are reliable, the
transcript only stretches word ends); see `without_word_starts`.
"""
import array
import math
import subprocess

WINDOW = 36            # frames looked at before a clip end / after a clip start
SPEECH_DB = 25
SILENCE_DB = 6
BREATH_MAX_DB = 18
MIN_BREATH = 3
MAX_BREATH = 18
KEEP_AFTER_VOICE = 2   # frames kept after the voice when cutting the breath


def frame_levels(path, start_s, n, fps):
    """Loudness (dB) of n consecutive frames of a media file from start_s, in one ffmpeg call."""
    rate = 16000
    out = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{max(start_s, 0):.5f}', '-t', f'{n / fps:.5f}', '-i', path,
                          '-vn', '-ac', '1', '-ar', str(rate), '-f', 's16le', '-'], capture_output=True).stdout
    a = array.array('h')
    a.frombytes(out[:len(out) // 2 * 2])
    per = rate / fps
    levels = []
    for k in range(n):
        seg = a[int(k * per):int((k + 1) * per)]
        rms = math.sqrt(sum((v / 32768) ** 2 for v in seg) / max(len(seg), 1))
        levels.append(20 * math.log10(rms + 1e-9))
    return levels


def breath_before_cut(levels):
    """`levels` run toward the cut (the last value is the frame just before the cut).
    -> dict(breath=frames of breath, after_voice=frames from the end of the voice to the cut) or None."""
    if not levels:
        return None
    floor = min(levels)
    speech = [i for i, v in enumerate(levels) if v >= floor + SPEECH_DB]
    if not speech:
        return None
    tail = levels[speech[-1] + 1:]
    quiet = next((i for i, v in enumerate(tail) if v < floor + SILENCE_DB), None)
    if quiet is None:
        return None                       # the voice never falls silent before the cut: end of a word, not a breath
    rest = tail[quiet:]
    soft = [i for i, v in enumerate(rest) if floor + SILENCE_DB <= v < floor + BREATH_MAX_DB]
    if len(soft) < MIN_BREATH or soft[-1] - soft[0] + 1 > MAX_BREATH or any(v >= floor + BREATH_MAX_DB for v in rest):
        return None
    return dict(breath=len(soft), after_voice=len(tail))


def _continues(a, b):
    return b.start == a.end and b.file == a.file and b.src_start == a.src_start + (a.end - a.start)


def find_breaths(snap, audio_track=1, levels=frame_levels, window=WINDOW):
    """[{frame, side, breath, after_voice}] for the voice track of a Snapshot. side = 'end' (breath before the end of
    a clip) or 'start' (breath at the start of a clip). Clips that simply continue the same take are not cuts."""
    voice = [c for c in snap.track('audio', audio_track) if c.has_media and c.file and c.start < snap.out_frame]
    found = []
    for i, c in enumerate(voice):
        fps = c.media_fps or 24
        length = c.end - c.start
        if length <= window:
            continue
        nxt = voice[i + 1] if i + 1 < len(voice) else None
        prv = voice[i - 1] if i > 0 else None
        if nxt is None or not _continues(c, nxt):
            end_s = (c.src_start + length) / fps
            b = breath_before_cut(levels(c.file, end_s - window / fps, window, fps))
            if b:
                found.append(dict(frame=c.end, side='end', **b))
        if prv is None or not _continues(prv, c):
            b = breath_before_cut(list(reversed(levels(c.file, c.src_start / fps, window, fps))))
            if b:
                found.append(dict(frame=c.start, side='start', **b))
    return found


def check_breaths(snap, values=None, levels=frame_levels, profiles=None):
    """Findings for the checks report (one per breath left at a cut)."""
    from .checks import finding
    out = []
    for b in find_breaths(snap, levels=levels):
        where = 'avant la coupe' if b['side'] == 'end' else 'au debut du plan'
        out.append(finding('breath', b['frame'], 1,
                           f"respiration de {b['breath']} images {where}; la voix est a {b['after_voice']} images de la coupe",
                           'warning'))
    return out


def without_word_starts(found, snap, store, audio_track=1):
    """Drop the candidates whose breath region holds the start of a transcribed word: that is speech, not a breath."""
    keep = []
    clips = [c for c in snap.track('audio', audio_track) if c.has_media and c.file]
    for b in found:
        lo, hi = (b['frame'] - b['after_voice'], b['frame']) if b['side'] == 'end' else (b['frame'], b['frame'] + b['after_voice'])
        hit = False
        for c in clips:
            if c.end <= lo or c.start >= hi:
                continue
            fps = c.media_fps or 24
            s0 = (c.src_start + max(lo, c.start) - c.start) / fps
            s1 = (c.src_start + min(hi, c.end) - c.start) / fps
            tr = store.load(c.file)
            if tr and any(s0 <= w['s'] < s1 for w in tr['words']):
                hit = True
        if not hit:
            keep.append(b)
    return keep


def plan_breath_trims(found, keep=KEEP_AFTER_VOICE):
    """Edits {P, Q, D: 0} removing the silence and the breath between the voice and the cut (all tracks ripple).
    Kept for tests and a FIRST edit only: do not use on a finished edit (it moves the picture) nor without
    `without_word_starts` (soft words were cut on Lili)."""
    edits = []
    for b in found:
        k = b['after_voice'] - keep
        if k <= 0:
            continue
        if b['side'] == 'end':
            edits.append(dict(P=b['frame'] - k, Q=b['frame'], D=0, text=f'respiration coupee ({k} images)'))
        else:
            edits.append(dict(P=b['frame'], Q=b['frame'] + k, D=0, text=f'respiration coupee ({k} images)'))
    return edits

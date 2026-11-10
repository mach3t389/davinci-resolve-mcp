"""Transcription anchored on SOURCE files (doc 25, section 4).

One transcript per source file, kept next to the project's rushes (`<project>/Transcriptions/<file>.words.json`).
Words carry SOURCE seconds. Any timeline (any version) re-places them from the clips' source positions, so a
timeline is never transcribed again after a cut, and markers can be re-anchored the same way.
"""
import hashlib
import json
import os
from pathlib import Path

from .model import FPS

SCHEMA = 1
_CHUNK = 1 << 20


def fingerprint(path):
    """Cheap identity of a media file that survives a move or rename: size + hash of the first and last MB."""
    p = Path(path)
    size = p.stat().st_size
    h = hashlib.sha1()
    with open(p, 'rb') as f:
        h.update(f.read(_CHUNK))
        if size > 2 * _CHUNK:
            f.seek(size - _CHUNK)
            h.update(f.read(_CHUNK))
    return dict(size=size, sha1=h.hexdigest())


class TranscriptStore:
    """Folder of `<media name>.words.json` files."""

    def __init__(self, folder):
        self.folder = Path(folder)

    def _path(self, media_path):
        return self.folder / (Path(media_path).name + '.words.json')

    def save(self, media_path, words, language='fr', model='', aligned=False, fps=None):
        """words: [{'w': 'bonjour', 's': 1.23, 'e': 1.60}] in SOURCE seconds, sorted."""
        self.folder.mkdir(parents=True, exist_ok=True)
        data = dict(schema=SCHEMA, file=Path(media_path).name, fingerprint=fingerprint(media_path), language=language,
                    model=model, aligned=aligned, fps=fps,
                    words=sorted(({'w': w['w'], 's': round(float(w['s']), 3), 'e': round(float(w['e']), 3)}
                                  for w in words), key=lambda w: w['s']))
        self._path(media_path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf8')
        return data

    def load(self, media_path):
        """Transcript of this media, or None. Matches by name first, then by fingerprint (file renamed/moved)."""
        want = None
        p = self._path(media_path)
        candidates = [p] if p.exists() else []
        if not candidates and self.folder.exists():
            candidates = sorted(self.folder.glob('*.words.json'))
        for c in candidates:
            try:
                data = json.loads(c.read_text(encoding='utf8'))
            except (OSError, ValueError):
                continue
            if want is None:
                want = fingerprint(media_path)
            if data.get('fingerprint') == want:
                return data
        return None

    def status(self, media_paths):
        """For a dashboard: how many of these sources have a transcript ('48 transcribed of 48')."""
        done = [m for m in media_paths if self.load(m)]
        return dict(total=len(media_paths), transcribed=len(done),
                    missing=[os.path.basename(m) for m in media_paths if m not in done])


# ---- words on a timeline ----------------------------------------------------------------------------------------
def voice_clips(snapshot, audio_tracks=(1,)):
    return [c for c in snapshot.clips if c.kind == 'audio' and c.track in audio_tracks and c.has_media and c.file]


def place_words(snapshot, store, audio_tracks=(1,)):
    """Words of the voice clips of a timeline, in TIMELINE frames: [{'f0','f1','w','file','src0'}], sorted.
    Never transcribes: a source without a transcript is reported in `missing`."""
    words, missing = [], set()
    for c in voice_clips(snapshot, audio_tracks):
        tr = store.load(c.file)
        if not tr:
            missing.add(c.file)
            continue
        fps = c.media_fps or FPS
        lo, hi = c.src_start, c.src_start + c.length
        for w in tr['words']:
            s0, s1 = int(round(w['s'] * fps)), max(int(round(w['e'] * fps)), int(round(w['s'] * fps)) + 1)
            if s0 >= lo and s0 < hi:                       # a word belongs to the clip where it starts
                f0 = c.start + (s0 - lo)
                words.append(dict(f0=f0, f1=min(f0 + (s1 - s0), c.end), w=w['w'], file=c.file, src0=s0))
    words.sort(key=lambda x: x['f0'])
    return words, sorted(missing)


def text_between(words, f0, f1):
    return ' '.join(w['w'].strip() for w in words if f0 <= w['f0'] < f1)


def silent_ranges(words, min_frames=6, start=0, end=None):
    """Gaps between words (timeline frames) of at least min_frames: candidate pauses/cut points."""
    out = []
    prev = start
    for w in words:
        if w['f0'] - prev >= min_frames:
            out.append((prev, w['f0']))
        prev = max(prev, w['f1'])
    if end is not None and end - prev >= min_frames:
        out.append((prev, end))
    return out


# ---- re-anchoring (markers, requests) --------------------------------------------------------------------------
def anchors_at(snapshot, f, audio_tracks=(1,)):
    """What is under timeline frame f in source terms: [('in'|'out', file, source_frame, at_clip_start)]."""
    out = []
    for c in voice_clips(snapshot, audio_tracks):
        if c.end == f:
            out.append(('out', c.file, c.src_start + c.length, False))
        if c.start <= f < c.end:
            out.append(('in', c.file, c.src_at(f), c.start == f))
    return out


def locate(snapshot, anchors, near=0, max_distance=24 * 120, audio_tracks=(1,)):
    """Best timeline frame in `snapshot` for a list of source anchors, or None.
    Prefers a position on a clip join (where a cut or a pause was placed), then the closest to `near`."""
    cands = []
    for an in anchors:
        kind, fi, sf = an[0], an[1], an[2]
        for c in voice_clips(snapshot, audio_tracks):
            if c.file != fi:
                continue
            s1 = c.src_start + c.length
            if kind == 'in' and c.src_start <= sf < s1:
                pos = c.start + sf - c.src_start
                at_join = pos == c.start
                cands.append((0 if (at_join or not an[3]) else 2, abs(pos - near), pos, 'start' if at_join else 'inside'))
            if kind == 'out' and c.src_start < sf <= s1:
                pos = c.start + sf - c.src_start
                cands.append((0 if pos == c.end else 1, abs(pos - near), pos, 'end' if pos == c.end else 'inside'))
    cands = [x for x in cands if x[1] < max_distance]
    return min(cands) if cands else None


def reanchor_markers(reference, markers_frames, target, near_map=None, audio_tracks=(1,)):
    """Re-place markers made on `reference` (a Snapshot) onto `target`, by source anchoring.
    markers_frames: {frame_in_reference: marker dict}. -> (placed {new_frame: marker}, unplaced [frames])."""
    placed, unplaced = {}, []
    for f, m in sorted(markers_frames.items()):
        near = (near_map or {}).get(f, f)
        hit = locate(target, anchors_at(reference, f, audio_tracks), near=near, audio_tracks=audio_tracks)
        if hit is None:
            unplaced.append(f)
        else:
            placed[hit[2]] = dict(m, anchor=hit[3])
    return placed, unplaced

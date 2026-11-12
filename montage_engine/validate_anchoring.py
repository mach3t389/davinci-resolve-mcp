"""Read-only validation of anchored transcripts on a real timeline.

Checks, without modifying anything in Resolve:
  1. coverage: every voice clip has a transcript; how much of the edit has words;
  2. independent anchoring check: the editor placed visuals/quotes on the moment their title is spoken. The word of that
     title, re-placed from the SOURCE transcript onto the timeline, must fall close to the clip's start;
  3. sound check: words sampled across the edit really sit on sound in the source file (not on silence).
Usage: python -m montage_engine.validate_anchoring "<timeline>" "<Transcriptions folder>" [--keywords file.json]
"""
import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from montage_engine import anchors  # noqa: E402
from montage_engine.checks import ffmpeg_level_db  # noqa: E402
from montage_engine.model import snapshot_from_resolve, tc  # noqa: E402
from montage_engine.series import role_of  # noqa: E402

# panel name prefix -> words that are spoken when the panel appears (Masterclass "Lili"; pass --keywords for others)
LILI_KEYWORDS = {
    'Visuels01': ['livres'], 'Visuels02': ['ernaux'], 'Visuels03': ['bonheur'], 'Visuels04': ['filles', 'march'],
    'Visuels05': ['martin'], 'Visuels06': ['patchett'], 'Visuels07': ['émissions', 'emissions'],
    'Lili12': ['doctorow'], 'Lili13': ['christie'], 'Lili18': ['oates'],
}


def norm(w):
    return re.sub(r'[^\wàâçéèêëîïôûùüÿœ]', '', w.lower())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('timeline')
    ap.add_argument('store')
    ap.add_argument('--keywords')
    ap.add_argument('--window', type=float, default=10.0, help='seconds around the clip start to look for the word')
    ap.add_argument('--sample', type=int, default=60)
    a = ap.parse_args(argv)
    import resolve_safe_mcp as r
    _, p = r.project()
    t = {x.GetName(): x for x in r.timelines(p)}[a.timeline]
    snap = snapshot_from_resolve(t)
    store = anchors.TranscriptStore(a.store)
    fps = snap.fps
    keywords = LILI_KEYWORDS
    if a.keywords:
        with open(a.keywords, encoding='utf8') as f:
            keywords = json.load(f)

    voice = [c for c in anchors.voice_clips(snap, (1,)) if c.start < snap.out_frame]
    words, missing = anchors.place_words(snap, store, (1,))
    covered = sum(c.length for c in voice)
    print(f'== {snap.name} | fin du montage {tc(snap.out_frame, fps)} | {len(voice)} plans de voix, {len(words)} mots replaces')
    print(f'sources de la voix sans transcription: {len(missing)}', [Path(m).name for m in missing][:5])

    # 1. coverage: seconds of voice clips holding at least one word per 3 s window
    gaps = anchors.silent_ranges(words, min_frames=fps * 12, start=0, end=snap.out_frame)
    print(f'plages de plus de 12 s sans mot: {len(gaps)}', [(tc(x, fps), round((y - x) / fps)) for x, y in gaps][:6])

    # 2. independent anchoring check with the editor's own visuals
    print('\n-- repere independant : le mot du titre vs le debut du visuel pose par Alexis')
    found = 0
    offsets = []
    rows = []
    for c in snap.clips:
        if c.kind != 'video' or not c.has_media or role_of(c.track_name) != 'graphic':
            continue
        key = next((k for k in keywords if c.name.startswith(k)), None)
        if not key:
            continue
        lo, hi = c.start - a.window * fps, c.start + a.window * fps
        cand = [w for w in words if lo <= w['f0'] <= hi and any(norm(w['w']).startswith(norm(k)) for k in keywords[key])]
        if not cand:
            rows.append((key, tc(c.start, fps), 'mot introuvable'))
            continue
        best = min(cand, key=lambda w: abs(w['f0'] - c.start))
        off = (best['f0'] - c.start) / fps
        offsets.append(off)
        found += 1
        rows.append((key, tc(c.start, fps), f'« {best["w"]} » a {tc(best["f0"], fps)} -> mot {off:+.1f} s par rapport au debut du visuel'))
    for row in rows:
        print('  ', *row)
    if offsets:
        print(f'   {found}/{len(rows)} retrouves; ecart median {sorted(offsets)[len(offsets) // 2]:+.1f} s, '
              f'min {min(offsets):+.1f} s, max {max(offsets):+.1f} s (le visuel apparait autour de la mention)')

    # 3. sound check: sampled words sit on sound in the source
    print('\n-- le son : mots pris au hasard, niveau dans le fichier source')
    random.seed(7)
    sample = random.sample(words, min(a.sample, len(words)))
    ref = []
    levels = []
    for w in sample:
        c = next(c for c in voice if c.file == w['file'] and c.start <= w['f0'] < c.end)
        fpsm = c.media_fps or fps
        s = w['src0'] / fpsm
        d = max((w['f1'] - w['f0']) / fps, 0.12)
        levels.append((ffmpeg_level_db(w['file'], s, d), w))
        ref.append(ffmpeg_level_db(w['file'], max(s - 2.5, 0), 2.0))
    base = sorted(v for v, _ in levels)[len(levels) // 2]
    quiet = [(round(v), w['w'], tc(w['f0'], fps)) for v, w in levels if v < base - 18]
    print(f'   niveau median des mots {base:.0f} dB; mots plus de 18 dB sous le median (probablement sur du silence): {len(quiet)}/{len(levels)}', quiet[:8])


if __name__ == '__main__':
    main()

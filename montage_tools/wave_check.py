"""Waveform check of every audio cut (A1) in the In-Out segment of a timeline: loudness of the last 2 frames
before the cut (outgoing clip) and the first 2 frames after it (incoming clip), compared with the speech level.
A cut is suspect when either side is loud (likely inside a word). Read-only."""
import json, os, subprocess, sys
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
import array, math, statistics

NAME = sys.argv[1]
_, p = r.project()
t = {x.GetName(): x for x in r.timelines(p)}[NAME]; st = t.GetStartFrame()
OUT = [int(k) for k, v in t.GetMarkers().items() if v['name'] == 'Out montage'][0]
A = [x for x in t.GetItemListInTrack('audio', 1) if x.GetStart() - st < OUT]
cache = {}
def pcm(path, start_s, dur_s):
    r_ = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{max(start_s, 0):.5f}', '-t', f'{dur_s:.5f}', '-i', path,
                         '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], capture_output=True)
    a = array.array('h'); a.frombytes(r_.stdout[:len(r_.stdout) // 2 * 2]); return [v / 32768 for v in a]
def db(x): return 20 * math.log10(math.sqrt(sum(v * v for v in x) / max(len(x), 1)) + 1e-9)
def tc(f): return f'{f // 1440:02d}:{(f % 1440) / 24:05.2f}'
rows, levels = [], []
for a, b in zip(A, A[1:]):
    cut = int(b.GetStart() - st)
    if int(a.GetEnd() - st) != cut: continue                      # pause gaps are not cuts inside speech
    ma, mb = a.GetMediaPoolItem(), b.GetMediaPoolItem()
    fa, fb = float(ma.GetClipProperty('FPS')), float(mb.GetClipProperty('FPS'))
    end_a = (a.GetLeftOffset() + a.GetDuration()) / fa; start_b = b.GetLeftOffset() / fb
    out_tail = pcm(ma.GetClipProperty('File Path'), end_a - 2 / fa, 2 / fa)
    in_head = pcm(mb.GetClipProperty('File Path'), start_b, 2 / fb)
    ctx = pcm(ma.GetClipProperty('File Path'), end_a - 3, 3)
    levels.append(db(ctx))
    rows.append(dict(cut=tc(cut), frame=cut, out_db=round(db(out_tail), 1), in_db=round(db(in_head), 1), speech_db=round(db(ctx), 1)))
ref = statistics.median(levels)
sus = [rw for rw in rows if max(rw['out_db'], rw['in_db']) > ref - 6]
print(json.dumps(dict(timeline=NAME, audio_cuts=len(rows), speech_level_db=round(ref, 1),
                      suspect=sus), ensure_ascii=False))

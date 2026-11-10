"""Render Audio 1 of a timeline over windows (frames) to wav files listed in chk.json. Read-only.
Usage: win_audio.py <timeline> <a:b> [<a:b> ...]"""
import json, os, subprocess, sys
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
S = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) == 'exemples_lili' else os.path.dirname(os.path.abspath(__file__)), 'work'); os.makedirs(S, exist_ok=True)
_, p = r.project()
t = {x.GetName(): x for x in r.timelines(p)}[sys.argv[1]]; st = t.GetStartFrame()
items = [(int(x.GetStart() - st), int(x.GetEnd() - st), x) for x in t.GetItemListInTrack('audio', 1) if x.GetMediaPoolItem()]
def tc(f): return f'{int(f) // 1440:02d}:{(int(f) % 1440) / 24:05.2f}'
chk = {}
for w in sys.argv[2:]:
    A, B = map(int, w.split(':')); parts, cur = [], A
    for a, b, x in items:
        if b <= A or a >= B: continue
        lo, hi = max(a, A), min(b, B)
        if lo > cur: parts.append(('sil', lo - cur))
        m = x.GetMediaPoolItem(); fps = float(m.GetClipProperty('FPS'))
        parts.append((m.GetClipProperty('File Path'), (x.GetLeftOffset() + lo - a) / fps, (hi - lo) / fps)); cur = hi
    if cur < B: parts.append(('sil', B - cur))
    files = []
    for k, pt in enumerate(parts):
        f = os.path.join(S, f'wa_{A}_{k}.wav')
        if pt[0] == 'sil':
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono', '-t', f'{pt[1] / 24:.6f}', '-c:a', 'pcm_s16le', f], check=True)
        else:
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{pt[1]:.6f}', '-t', f'{pt[2]:.6f}', '-i', pt[0], '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', f], check=True)
        files.append(f)
    lst = os.path.join(S, 'wa_list.txt')
    open(lst, 'w', encoding='utf8').write(''.join(f"file '{x.replace(os.sep, '/')}'\n" for x in files))
    out = os.path.join(S, f'win_{A}.wav')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy', out], check=True)
    for f in files: os.remove(f)
    chk[f'{tc(A)}-{tc(B)}'] = out
json.dump(chk, open(os.path.join(S, 'chk.json'), 'w'))
print(list(chk))

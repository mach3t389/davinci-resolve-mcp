"""Camera tidy after a rebuild: (1) a V2 piece < 6 frames takes the camera of the neighbour whose source it continues;
(2) explicit flips given as source offsets on V2 (file-independent offset match). Usage: cam_tidy.py <timeline> [offset=True|False ...]"""
import sys
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
_, p = r.project(); t = {x.GetName(): x for x in r.timelines(p)}[sys.argv[1]]; st = t.GetStartFrame()
def tc(f): return f'{int(f) // 1440:02d}:{(int(f) % 1440) / 24:05.2f}'
V = [x for x in t.GetItemListInTrack('video', 2) if x.GetMediaPoolItem()]
def cont(a, b): return (a.GetMediaPoolItem().GetUniqueId() == b.GetMediaPoolItem().GetUniqueId() and a.GetEnd() == b.GetStart()
                        and int(b.GetLeftOffset()) == int(a.GetLeftOffset() + a.GetDuration()))
for k, x in enumerate(V):
    if x.GetDuration() >= 6: continue
    pv = V[k - 1] if k else None; nx = V[k + 1] if k + 1 < len(V) else None
    ref = pv if pv and cont(pv, x) else (nx if nx and cont(x, nx) else None)
    if ref and x.GetClipEnabled() != ref.GetClipEnabled():
        x.SetClipEnabled(ref.GetClipEnabled()); print('bout', tc(x.GetStart() - st), '->', ref.GetClipEnabled())
for arg in sys.argv[2:]:
    off, en = arg.split('='); x = next(x for x in V if int(x.GetLeftOffset()) == int(off))
    x.SetClipEnabled(en == 'True'); print('bascule', tc(x.GetStart() - st), en)

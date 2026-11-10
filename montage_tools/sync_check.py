"""A/V sync audit: for every Audio 1 piece, the camera pictures (V1 and V2) over the same frames must come from the
same take at the same source frame (V2 = other camera: compared to its Audio 2 twin). Read-only. Usage: sync_check.py <timeline>"""
import sys, json
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
_, p = r.project(); t = {x.GetName(): x for x in r.timelines(p)}[sys.argv[1]]; st = t.GetStartFrame()
def tc(f): return f'{int(f) // 1440:02d}:{(int(f) % 1440) / 24:05.2f}'
def L(k, i):
    return [(int(x.GetStart() - st), int(x.GetEnd() - st), x.GetMediaPoolItem().GetName(), int(x.GetLeftOffset()))
            for x in t.GetItemListInTrack(k, i) if x.GetMediaPoolItem()]
OUT = next((int(float(k)) for k, v in t.GetMarkers().items() if v["name"] == "Out"), int(t.GetEndFrame() - st))   # whole timeline without an Out marker
bad = []
for ka, kv in ((1, 1), (2, 2)):
    A, V = L('audio', ka), L('video', kv)
    for a, b, fa, sa in A:
        if a >= OUT: break
        for c, d, fv, sv in V:
            lo, hi = max(a, c), min(b, d)
            if lo >= hi: continue
            da = sa + lo - a; dv = sv + lo - c
            if fa != fv or abs(da - dv) > 1:
                bad.append((lo, hi, f'A{ka}/V{kv}', fa[-8:], da, fv[-8:], dv))
    # audio present but no picture
    for a, b, fa, sa in A:
        if a >= OUT: break
        cov = sorted((max(a, c), min(b, d)) for c, d, *_ in V if max(a, c) < min(b, d))
        cur = a
        for lo, hi in cov:
            if lo > cur: bad.append((cur, lo, f'V{kv} vide sous A{ka}', fa[-8:], 0, '', 0))
            cur = max(cur, hi)
        if cur < b: bad.append((cur, b, f'V{kv} vide sous A{ka}', fa[-8:], 0, '', 0))
bad.sort()
for lo, hi, k, fa, da, fv, dv in bad: print(tc(lo), hi - lo, 'im.', k, fa, da, '->', fv, dv)
print('total', len(bad), 'start TC', t.GetStartTimecode())

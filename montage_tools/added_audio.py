"""For a chain of timelines, list the source audio (Audio 1, before Out) that appears in a version but not in the
previous one: ranges >= 6 frames, with their position. Read-only."""
import sys
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
_, p = r.project(); ts = {x.GetName(): x for x in r.timelines(p)}
def tc(f): return f'{int(f) // 1440:02d}:{(int(f) % 1440) / 24:05.2f}'
def frames(t):
    st = t.GetStartFrame(); OUT = [int(float(k)) for k, v in t.GetMarkers().items() if v['name'] in ('Out',)]
    OUT = OUT[0] if OUT else 10 ** 9; d = {}
    for x in t.GetItemListInTrack('audio', 1):
        m = x.GetMediaPoolItem()
        if not m: continue
        a = int(x.GetStart() - st)
        if a >= OUT: break
        for k in range(int(x.GetDuration())): d[(m.GetName(), int(x.GetLeftOffset()) + k)] = a + k
    return d
names = sys.argv[1:]
prev = frames(ts[names[0]])
for n in names[1:]:
    cur = frames(ts[n]); add = sorted(v for k, v in cur.items() if k not in prev)
    runs = []
    for f in add:
        if runs and f == runs[-1][1]: runs[-1][1] = f + 1
        else: runs.append([f, f + 1])
    print('==', n, [(tc(a), b - a) for a, b in runs if b - a >= 6])
    prev = cur

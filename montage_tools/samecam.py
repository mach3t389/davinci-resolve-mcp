"""Find visible jump cuts on the SAME camera after 11:05 and fix them by switching camera (enable/disable the
camera-1 piece above). Constraints: shots >= 2 s, no cut within 1 s before a visual fade-in nor inside any fade,
not at a b-roll edge. plan: report only. apply: duplicate the source as V3.5 and apply. Read-only in plan mode."""
import json, sys, time
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
SRC, NEW = sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None
START, MIN = int(sys.argv[4]) if len(sys.argv) > 4 else 0, 48
mode = sys.argv[1]
_, p = r.project(); ts = {t.GetName(): t for t in r.timelines(p)}
t = ts[SRC]
if mode == 'apply':
    if NEW in ts: raise SystemExit('exists')
    t = t.DuplicateTimeline(NEW); p.SetCurrentTimeline(t); time.sleep(2)
st = t.GetStartFrame()
OUT = [int(float(k)) for k, v in t.GetMarkers().items() if v['name'] == 'Out'][0]
def tc(f): return f'{int(f) // 1440:02d}:{(int(f) % 1440) / 24:05.2f}'
V1, V2 = t.GetItemListInTrack('video', 1), t.GetItemListInTrack('video', 2)
def at(tr, f):
    for x in tr:
        if x.GetStart() - st <= f < x.GetEnd() - st: return x
def cam(f):
    x = at(V2, f); return None if x is None else (1 if x.GetClipEnabled() else 2)
def vis_item(f):
    x2, x1 = at(V2, f), at(V1, f)
    if not x2 or not x1: return None
    return x2 if x2.GetClipEnabled() else x1
cover, fades = [], []           # full-screen covers (b-roll, panels) and visual fade zones
for i in range(3, t.GetTrackCount('video') + 1):
    name = t.GetTrackName('video', i)
    for x in t.GetItemListInTrack('video', i):
        if not x.GetMediaPoolItem(): continue
        a, b = int(x.GetStart() - st), int(x.GetEnd() - st)
        if x.GetName().startswith('Visuels'):
            fades.append((a - 24, a + 32)); fades.append((b - 36, b + 12)); cover.append((a + 20, b - 24))
        else:
            cover.append((a, b))
            if name == "Plans d'appui": fades.append((a - 12, a + 12)); fades.append((b - 12, b + 12))
def covered(f): return any(a <= f < b for a, b in cover)
def in_fade(f): return any(a < f < b for a, b in fades)
# real visible jump cuts (same camera on both sides, picture source jumps)
bounds = sorted({int(x.GetStart() - st) for x in V2 if START <= x.GetStart() - st < OUT})
same = []
for c in bounds:
    a, b = vis_item(c - 1), vis_item(c)
    if not a or not b or cam(c - 1) != cam(c): continue
    if a.GetMediaPoolItem().GetUniqueId() == b.GetMediaPoolItem().GetUniqueId() and b.GetLeftOffset() == a.GetLeftOffset() + (c - int(a.GetStart() - st)):
        continue                                               # same take continues: not a cut
    if covered(c) or covered(c - 1): continue                  # hidden under b-roll / panel
    same.append(c)
def runs():
    rs = []
    for x in V2:
        s, e = int(x.GetStart() - st), int(x.GetEnd() - st)
        if e <= START or s >= OUT: continue
        c = 1 if x.GetClipEnabled() else 2
        if rs and rs[-1][2] == c and rs[-1][1] == s: rs[-1][1] = e; rs[-1][3].append(x)
        else: rs.append([s, e, c, [x]])
    return rs
print('meme camera:', [tc(c) for c in same]); fixed, unresolved = [], []
for c in same:
    if cam(c - 1) != cam(c): continue                          # already solved by a previous flip
    rs = runs()
    k = next(i for i, q in enumerate(rs) if q[0] <= c < q[1])
    q = rs[k]
    # flip the pieces from c up to a boundary that keeps every shot >= 2 s and puts no switch in a fade zone
    pieces = [x for x in q[3] if x.GetStart() - st >= c]
    best = None
    for j in range(len(pieces)):
        end = int(pieces[j].GetEnd() - st)
        left_len = c - q[0]; flip_len = end - c; right_len = q[1] - end
        if left_len < MIN or flip_len < MIN: continue
        if 0 < right_len < MIN: continue
        if in_fade(c) or (right_len > 0 and in_fade(end)): continue
        if right_len == 0:                                      # flip reaches the run end: merges with next run
            nxt = rs[k + 1] if k + 1 < len(rs) and rs[k + 1][0] == q[1] else None
            if nxt and nxt[2] != q[2]: pass
        best = (j, end); break
    if best is None:                                            # try the other side: flip pieces ending at c
        before = [x for x in q[3] if x.GetEnd() - st <= c][::-1]
        for j in range(len(before)):
            beg = int(before[j].GetStart() - st)
            left_len = beg - q[0]; flip_len = c - beg; right_len = q[1] - c
            if flip_len < MIN or right_len < MIN or (0 < left_len < MIN): continue
            if in_fade(c) or (left_len > 0 and in_fade(beg)): continue
            prv = rs[k - 1] if k > 0 and rs[k - 1][1] == q[0] else None
            if left_len == 0 and prv and prv[2] != q[2]: continue   # would merge into a same-camera jump on the left
            best = ('L', j, beg); break
        if best is None:
            unresolved.append(tc(c)); continue
        for x in before[:best[1] + 1]:
            if mode == 'apply': x.SetClipEnabled(q[2] != 1)
        fixed.append((tc(best[2]), tc(c))); continue
    for x in pieces[:best[0] + 1]:
        if mode == 'apply': x.SetClipEnabled(q[2] != 1)
    fixed.append((tc(c), tc(best[1])))
print(json.dumps(dict(mode=mode, same_camera_jump_cuts=len(same), fixed=fixed, unresolved=unresolved), ensure_ascii=False))

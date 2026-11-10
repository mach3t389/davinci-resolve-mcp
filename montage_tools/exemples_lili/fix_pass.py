"""V3.9 from the current V3.8 (keeps Alexis's edits):
1. picture/sound sync: wherever Audio 1/2 has sound, the camera picture (V1/V2) shows the same take at the same frame;
2. remnant removals (ripple): audio ranges and the matching camera ranges (camera keeps its 12-frame head under the panel);
3. panel chains: when panel B starts less than 1 s after panel A ends, B starts CHAIN frames before A ends, above it.
Usage: fix_pass.py plan|apply"""
import json, os, sys, time
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
import opentimelineio as otio
S = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) == 'exemples_lili' else os.path.dirname(os.path.abspath(__file__)), 'work'); os.makedirs(S, exist_ok=True)
SRC, NEW, FPS, CHAIN = 'Lili V3.8 - citations et pauses replacees', 'Lili V3.9 - image synchro et panneaux enchaines', 24, 16
MODE = sys.argv[1]
resolve, p = r.project(); ts = {t.GetName(): t for t in r.timelines(p)}
if MODE == 'apply' and NEW in ts: raise SystemExit('exists')
src = ts[SRC]; st = src.GetStartFrame()
def tc(f): return f'{int(f) // 1440:02d}:{(int(f) % 1440) / 24:05.2f}'
OUT = [int(float(k)) for k, v in src.GetMarkers().items() if v['name'] == 'Out'][0]
def api(k, i):
    return [(int(x.GetStart() - st), int(x.GetEnd() - st), x.GetMediaPoolItem().GetName(), int(x.GetLeftOffset()))
            for x in src.GetItemListInTrack(k, i) if x.GetMediaPoolItem()]
# ---- removals: (audio range, camera range) of equal length; found by content, not hard-coded frames ----
A1 = api('audio', 1)
REM = []
for a, b, f, s0 in A1:
    if f.endswith('C015.mov') and s0 in (7469, 7520) and b - a in (19, 3):
        cam = next((c, d) for c, d, g, t0 in api('video', 1) if c < a <= d) if s0 == 7469 else None
        if s0 == 7469: REM.append(((a, b), (a + 12, b + 12)))                    # keep the 12-frame head
        else:
            tail = next(c for c, d, g, t0 in api('video', 1) if g.endswith('C015.mov') and t0 + (a - c) == 7681 - 0 or (c <= a < d and t0 == 7661))
            REM.append(((a, b), (tail, tail + (b - a))))                             # trim the tail start
# ---- tiny audio remnants (<= 3 frames, source-contiguous with neither neighbour): absorbed by the adjacent shot ----
ABS = []
for k, (a, b, f, s0) in enumerate(A1):
    if b - a > 3 or a >= OUT: continue
    pv = A1[k - 1] if k else None; nx = A1[k + 1] if k + 1 < len(A1) else None
    if (pv and pv[1] == a and pv[2] == f and pv[3] + pv[1] - pv[0] == s0) or (nx and nx[0] == b and nx[2] == f and nx[3] == s0 + b - a): continue
    if any(x[0][0] <= a < x[0][1] for x in REM): continue
    if nx and nx[0] == b: ABS.append((a, b, 'next'))
    elif pv and pv[1] == a: ABS.append((a, b, 'prev'))
print('miettes', [(tc(a), b - a, w) for a, b, w in ABS])
print('retraits', [(tc(x[0][0]), x[0][1] - x[0][0], tc(x[1][0])) for x in REM])
# ---- sync mismatches (old frames), outside removals ----
MIS = {}
for ka, kv in ((1, 1), (2, 2)):
    A, V = api('audio', ka), api('video', kv); out = []
    for a, b, fa, sa in A:
        if a >= OUT: break
        for c, d, fv, sv in V:
            lo, hi = max(a, c), min(b, d)
            if lo >= hi: continue
            if any(x[0][0] <= lo < x[0][1] for x in REM) or any(u <= lo < v for u, v, _ in ABS): continue
            da, dv = sa + lo - a, sv + lo - c
            if fa != fv: raise SystemExit(f'autre fichier {tc(lo)}')
            if da != dv and abs(da - dv) > 1: out.append((lo, hi, da - dv))
    MIS[kv] = out
print('synchro', {k: [(tc(a), b - a) for a, b, _ in v] for k, v in MIS.items()})
path_in = os.path.join(S, 'fix_in.otio')
if not src.Export(path_in, resolve.EXPORT_OTIO): raise SystemExit('export failed')
tl = otio.adapters.read_from_file(path_in)
def fr(t):
    v = t.rescaled_to(FPS).value; assert abs(v - round(v)) < 1e-6, v; return int(round(v))
END = fr(tl.duration())
def kept(rems):
    ks, prev = [], 0
    for a, b in sorted(rems): ks.append((prev, a)); prev = b
    ks.append((prev, END)); return ks
KA = kept([x[0] for x in REM])
def fwd(f, ks=KA):
    pos = 0
    for a, b in ks:
        if a <= f < b or (f == b == END): return pos + f - a
        if f < a: return pos
        pos += b - a
NEWEND = sum(b - a for a, b in KA)
def gap(n): return otio.schema.Gap(duration=otio.opentime.RationalTime(n, FPS))
def cut(item, rel, n):
    pc = item.deepcopy(); s0 = item.trimmed_range().start_time
    pc.source_range = otio.opentime.TimeRange(s0 + otio.opentime.RationalTime(rel, s0.rate), otio.opentime.RationalTime(n, s0.rate))
    pe = pc.source_range.start_time + pc.source_range.duration
    mks = list(pc.markers); pc.markers.clear()
    for mk in mks:
        if pc.source_range.start_time <= mk.marked_range.start_time < pe: pc.markers.append(mk.deepcopy())
    pc.metadata.get('Resolve_OTIO', {}).pop('Link Group ID', None)
    return pc
# graphics: chains
graphics = []
for ti, orig in enumerate(tl.tracks):
    if orig.kind == 'Video' and not orig.name.startswith('Camera') and orig.name != "Plans d'appui":
        c = 0
        for it in orig:
            if isinstance(it, otio.schema.Transition): continue
            L = fr(it.duration())
            if isinstance(it, otio.schema.Clip): graphics.append([fwd(c), fwd(c) + L, ti, it.name])
            c += L
graphics.sort()
MOVE = {}
PROF = json.load(open(os.path.join(S, 'alpha_profiles.json')))   # from alpha_profile.py
gi = []                                                     # graphic items from the API: start, end, track, name, offset, media, has dissolve
for i in range(3, src.GetTrackCount('video') + 1):
    if src.GetTrackName('video', i) == "Plans d'appui": continue
    its = src.GetItemListInTrack('video', i); tr = {int(x.GetStart()) for x in its if not x.GetMediaPoolItem()}
    for x in its:
        m = x.GetMediaPoolItem()
        if m: gi.append((fwd(int(x.GetStart() - st)), fwd(int(x.GetStart() - st)) + int(x.GetDuration()), i, x.GetName(),
                         int(x.GetLeftOffset()), m.GetName(), int(x.GetStart()) in tr))
gi.sort()
for A, B in zip(gi, gi[1:]):
    if A[3].split('_v')[0] == B[3].split('_v')[0] or B[2] <= A[2] or not (0 < B[0] - A[1] < 24): continue
    if A[3].startswith('Visuels') or B[3].startswith('Visuels'): continue   # visuals follow the speech: never moved for a chain
    pa, pb = PROF.get(A[5]), PROF.get(B[5])
    a_out = A[0] + (pa['out_start'] - A[4]) if pa else A[1] - CHAIN          # timeline frame where A starts to fade
    b_in = 12 if B[6] else (pb['in_full'] if pb else CHAIN)                   # frames B needs to be fully visible
    MOVE[(B[2] - 1, B[3])] = min(B[0], a_out - b_in)
print('enchainements', [(n[:24], tc(v)) for (i, n), v in MOVE.items()])
print('durees', [(g[3][:20], tc(g[0]), g[1]-g[0], g[4]) for g in gi if g[3][:9] in ('Visuels01','Visuels07','Lili17-Li','Lili25-Mo')])
out = tl.deepcopy(); out.name = NEW
for ti, (orig, target) in enumerate(zip(tl.tracks, out.tracks)):
    target.clear()
    items, c = [], 0
    for it in orig:
        if isinstance(it, otio.schema.Transition):
            if orig.kind == 'Video' and not orig.name.startswith('Camera'): continue
            raise SystemExit(f'transition on {orig.name}')
        L = fr(it.duration()); items.append((c, c + L, it)); c += L
    if orig.kind == 'Video' and not orig.name.startswith('Camera'):
        placed = []
        for a, b, it in items:
            if not isinstance(it, otio.schema.Clip): continue
            vt = sum(1 for t_ in list(tl.tracks)[:ti + 1] if t_.kind == 'Video') - 1
            new = MOVE.get((vt, it.name), fwd(a)); placed.append((new, b - a, it))
        placed.sort(key=lambda x: x[0]); c = 0
        for new, L, it in placed:
            if new < c: raise SystemExit(f'{orig.name} overlap at {it.name} {tc(new)}')
            if new > c: target.append(gap(new - c))
            target.append(it.deepcopy()); c = new + L
        if c < NEWEND: target.append(gap(NEWEND - c))
        continue
    cam = orig.kind == 'Video'
    vidx = sum(1 for t_ in list(tl.tracks)[:ti + 1] if t_.kind == 'Video') if cam else None
    mis = MIS.get(vidx, []) if cam else []
    # pieces in old frames, with sync replacements
    pieces = []
    for a, b, it in items:
        cuts = sorted({a, b} | {x for lo, hi, _ in mis for x in (lo, hi) if a < x < b})
        for u, v in zip(cuts, cuts[1:]):
            if isinstance(it, otio.schema.Gap): pieces.append((u, v, gap(v - u))); continue
            sh = next((d for lo, hi, d in mis if lo <= u < hi), 0)
            pieces.append((u, v, it.deepcopy() if (u, v) == (a, b) and not sh else cut(it, u - a + sh, v - u)))
    def at(f): return next(((a, b, it) for a, b, it in items if a <= f < b), None)
    for u, v, w in ABS:                                     # replace the remnant by the adjacent shot on this track
        nb = at(v) if w == 'next' else at(u - 1)
        if nb is None or not isinstance(nb[2], otio.schema.Clip): continue
        rep = cut(nb[2], u - nb[0], v - u)
        new = []
        for x0, x1, pc in pieces:
            if x1 <= u or x0 >= v: new.append((x0, x1, pc)); continue
            if x0 < u: new.append((x0, u, cut(pc, 0, u - x0) if not isinstance(pc, otio.schema.Gap) else gap(u - x0)))
            if x1 > v: new.append((v, x1, cut(pc, v - x0, x1 - v) if not isinstance(pc, otio.schema.Gap) else gap(x1 - v)))
        new.append((u, v, rep)); pieces = sorted(new, key=lambda z: z[0])
    ks = kept([x[1] for x in REM]) if cam else KA
    for k0, k1 in ks:
        for u, v, pc in pieces:
            lo, hi = max(u, k0), min(v, k1)
            if lo >= hi: continue
            if isinstance(pc, otio.schema.Gap): target.append(gap(hi - lo))
            elif (lo, hi) == (u, v): target.append(pc)
            else: target.append(cut(pc, lo - u, hi - lo))
lens = {t.name: fr(t.duration()) for t in out.tracks}
if set(lens.values()) != {NEWEND}: raise SystemExit(f'track lengths differ: {lens}')
out.tracks.markers.clear()
for mk in tl.tracks.markers:
    nm = mk.deepcopy(); nm.marked_range = otio.opentime.TimeRange(otio.opentime.RationalTime(fwd(fr(mk.marked_range.start_time)), FPS), mk.marked_range.duration)
    out.tracks.markers.append(nm)
print(json.dumps(dict(END=END, NEWEND=NEWEND)))
if MODE != 'apply': raise SystemExit
path_out = os.path.join(S, 'fix_out.otio'); otio.adapters.write_to_file(out, path_out)
used = {x.GetMediaPoolItem().GetUniqueId() for k in ('video', 'audio') for i in range(1, src.GetTrackCount(k) + 1)
        for x in (src.GetItemListInTrack(k, i) or []) if x.GetMediaPoolItem()}
folders = []
def walk(fo):
    if any(c_.GetUniqueId() in used for c_ in (fo.GetClipList() or [])): folders.append(fo)
    for s_ in fo.GetSubFolderList() or []: walk(s_)
walk(p.GetMediaPool().GetRootFolder())
created = p.GetMediaPool().ImportTimelineFromFile(path_out, {'timelineName': NEW, 'importSourceClips': False, 'sourceClipsFolders': folders})
if not created: raise SystemExit('import failed')
time.sleep(1)
if created.GetStartTimecode() != src.GetStartTimecode(): created.SetStartTimecode(src.GetStartTimecode())
cst = created.GetStartFrame()
new_ids = {x.GetMediaPoolItem().GetUniqueId() for k in ('video', 'audio') for i in range(1, created.GetTrackCount(k) + 1)
           for x in (created.GetItemListInTrack(k, i) or []) if x.GetMediaPoolItem()}
if not new_ids <= used: raise SystemExit('import created new media; inspect.')
KC = kept([x[1] for x in REM])
def back(nf, ks):
    pos = 0
    for a, b in ks:
        if pos <= nf < pos + b - a: return a + nf - pos
        pos += b - a
for i in range(1, created.GetTrackCount('video') + 1):
    old = [(int(x.GetStart() - st), int(x.GetEnd() - st), x.GetClipColor(), x.GetClipEnabled()) for x in src.GetItemListInTrack('video', i) if x.GetMediaPoolItem()]
    for x in created.GetItemListInTrack('video', i):
        if not x.GetMediaPoolItem(): continue
        if created.GetTrackName('video', i) == "Plans d'appui": x.SetClipColor('Green'); continue
        if i >= 4: x.SetClipColor('Purple'); continue
        f0 = back(int(x.GetStart() - cst), KC if i <= 2 else KA)
        hit = next(((c_, en) for a, b, c_, en in old if f0 is not None and a <= f0 < b), None)
        if hit:
            if hit[0]: x.SetClipColor(hit[0])
            x.SetClipEnabled(hit[1])
p.SetCurrentTimeline(created)
print('cree', created.GetName(), int(created.GetEndFrame() - cst), 'attendu', NEWEND)

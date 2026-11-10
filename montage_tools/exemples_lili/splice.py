"""V3.8 from V3.7: rebuild the timeline as a list of segments (old-timeline ranges, fresh source pieces, panel
pauses with same-take video under the fades). Graphic tracks follow their content; named panels are re-placed.
Usage: splice.py plan|apply"""
import json, os, sys, time, subprocess
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
import opentimelineio as otio

S = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) == 'exemples_lili' else os.path.dirname(os.path.abspath(__file__)), 'work'); os.makedirs(S, exist_ok=True)
SRC, NEW, FPS = 'Lili V3.7 - bouts courts sur l autre camera', 'Lili V3.8 - citations et pauses replacees', 24
MODE = sys.argv[1]
HEAD, TAIL = 12, 20
resolve, p = r.project()
ts = {t.GetName(): t for t in r.timelines(p)}
if MODE == 'apply' and NEW in ts: raise SystemExit('exists')
src = ts[SRC]; st = src.GetStartFrame()
def tc(f): return f'{int(f) // 1440:02d}:{(int(f) % 1440) / 24:05.2f}'
def api_at(kind, i, f):
    for x in src.GetItemListInTrack(kind, i):
        if x.GetStart() - st <= f < x.GetEnd() - st: return x

# ---- pause before "Quantifier": pick the quietest frame just before the word (11489) ----
a1 = api_at('audio', 1, 11489); m = a1.GetMediaPoolItem(); fps_m = float(m.GetClipProperty('FPS'))
off = int(a1.GetLeftOffset()) - int(a1.GetStart() - st)
raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{(11480 + off) / fps_m:.5f}', '-t', f'{10 / fps_m:.5f}', '-i',
                      m.GetClipProperty('File Path'), '-vn', '-ac', '1', '-ar', '48000', '-f', 'f32le', '-'], capture_output=True).stdout
import array, math
sm = array.array('f', raw); n = int(48000 / fps_m)
lv = [20 * math.log10(math.sqrt(sum(v * v for v in sm[i * n:(i + 1) * n]) / n) + 1e-9) for i in range(10)]
X1 = 11480 + min(range(4, 10), key=lambda i: lv[i])
print('pause Quantifier a', X1, tc(X1), [round(v) for v in lv])

PANEL1 = next(x.GetName() for x in src.GetItemListInTrack('video', 4) if int(x.GetStart() - st) == 11513 and x.GetMediaPoolItem())
PANEL2 = next(x.GetName() for x in src.GetItemListInTrack('video', 4) if int(x.GetStart() - st) == 21077 and x.GetMediaPoolItem())
PANEL3 = next(x.GetName() for x in src.GetItemListInTrack('video', 4) if int(x.GetStart() - st) == 43695 and x.GetMediaPoolItem())
SEG = [('tl', 0, X1), ('pause', X1, 287, PANEL1), ('tl', X1, 11513), ('tl', 11800, 21077), ('tl', 21364, 21637),
       ('pause', 21637, 287, PANEL2), ('tl', 21637, 27989), ('src', 27989, 41890, [(41838, 52)]),
       ('tl', 27989, 32303), ('src', 32303, 47433, [(47433, 376), (47943, 261)]), ('tl', 33158, 43695),
       ('pause', 43695, 125, PANEL3, 43975, 4822 - 4934), ('src', 43975, 4934, [(4822, 120)]), ('tl', 43983, None)]
# quote panels: offsets from the start of the restored quote block (old 32303 = block start)
QUOTE = {'Lili12-Citation-Doctorow': (32303, 32387 - 32303), 'Lili13-Citation-Christie': (32303, 32628 - 32303),
         'Lili18-Citation-Oates': (43975, 0)}

path_in = os.path.join(S, 'splice_in.otio')
if not src.Export(path_in, resolve.EXPORT_OTIO): raise SystemExit('export failed')
tl = otio.adapters.read_from_file(path_in)
def fr(t):
    v = t.rescaled_to(FPS).value; assert abs(v - round(v)) < 1e-6, v; return int(round(v))
END = fr(tl.duration())
SEG[-1] = ('tl', 43983, END)
# new positions
pos, layout = 0, []
for sg in SEG:
    L = sg[2] - sg[1] if sg[0] == 'tl' else (sg[2] if sg[0] == 'pause' else sum(n for _, n in sg[3]))
    layout.append((pos, L, sg)); pos += L
NEWEND = pos
def fwd(f):
    for p0, L, sg in layout:
        if sg[0] == 'tl' and sg[1] <= f < sg[2]: return p0 + f - sg[1]
    if f == END: return NEWEND
def back(nf):
    for p0, L, sg in layout:
        if p0 <= nf < p0 + L: return sg, nf - p0
pause_new = {sg[3]: p0 for p0, L, sg in layout if sg[0] == 'pause'}
src_new = {sg[1]: p0 for p0, L, sg in layout if sg[0] == 'src'}
def gap(n): return otio.schema.Gap(duration=otio.opentime.RationalTime(n, FPS))
def cut(item, rel, n):
    """copy of clip `item` whose source starts `rel` frames after its current start (may be <0 or past its end)"""
    pc = item.deepcopy(); s0 = item.trimmed_range().start_time
    pc.source_range = otio.opentime.TimeRange(s0 + otio.opentime.RationalTime(rel, s0.rate), otio.opentime.RationalTime(n, s0.rate))
    ps = pc.source_range.start_time; pe = ps + pc.source_range.duration
    mks = list(pc.markers); pc.markers.clear()
    for mk in mks:
        if ps <= mk.marked_range.start_time < pe: pc.markers.append(mk.deepcopy())
    pc.metadata.get('Resolve_OTIO', {}).pop('Link Group ID', None)
    return pc
out = tl.deepcopy(); out.name = NEW
report = []
for orig, target in zip(tl.tracks, out.tracks):
    target.clear()
    items, c = [], 0
    for it in orig:
        if isinstance(it, otio.schema.Transition):
            if orig.kind == 'Video' and not orig.name.startswith('Camera'): continue
            raise SystemExit(f'transition on {orig.name}')
        L = fr(it.duration()); items.append((c, c + L, it)); c += L
    def at(f):
        return next(((a, b, it) for a, b, it in items if a <= f < b), None)
    if orig.kind == 'Video' and not orig.name.startswith('Camera'):
        placed = []
        for a, b, it in items:
            if not isinstance(it, otio.schema.Clip): continue
            base = it.name.split('_v')[0]
            if it.name in pause_new: new = pause_new[it.name]
            elif base in QUOTE: new = src_new[QUOTE[base][0]] + QUOTE[base][1]
            else:
                new = fwd(a)
                if new is None: raise SystemExit(f'{orig.name}: {it.name} at {tc(a)} falls in a removed range')
            placed.append((new, b - a, it))
        placed.sort(key=lambda x: x[0]); c = 0
        for new, L, it in placed:
            if new < c: raise SystemExit(f'{orig.name} overlap at {it.name} {tc(new)}')
            if new > c: target.append(gap(new - c))
            target.append(it.deepcopy()); c = new + L
        if c < NEWEND: target.append(gap(NEWEND - c))
        continue
    cam = orig.kind == 'Video'
    for p0, L, sg in layout:
        if sg[0] == 'tl':
            a0, b0 = sg[1], sg[2]
            for a, b, it in items:
                lo, hi = max(a, a0), min(b, b0)
                if lo >= hi: continue
                if isinstance(it, otio.schema.Gap): target.append(gap(hi - lo))
                elif lo == a and hi == b: target.append(it.deepcopy())
                else: target.append(cut(it, lo - a, hi - lo))
        elif sg[0] == 'pause':
            f, D = sg[1], sg[2]
            if not cam: target.append(gap(D)); continue
            pa, pb = at(f - 1), at(f)
            assert isinstance(pa[2], otio.schema.Clip), (orig.name, f)
            target.append(cut(pa[2], f - pa[0], HEAD)); target.append(gap(D - HEAD - TAIL))
            if len(sg) > 4:
                pb = at(sg[4]); assert isinstance(pb[2], otio.schema.Clip), (orig.name, sg[4])
                target.append(cut(pb[2], (sg[4] - pb[0]) + sg[5] - TAIL, TAIL))
            else: target.append(cut(pb[2], f - pb[0] - TAIL, TAIL))
        else:
            f, api0, pieces = sg[1], sg[2], sg[3]
            t0 = at(f)
            if t0 is None or not isinstance(t0[2], otio.schema.Clip): raise SystemExit(f'no template on {orig.name} at {f}')
            for s, n in pieces: target.append(cut(t0[2], (f - t0[0]) + (s - api0), n))
    report.append((orig.name, fr(target.duration())))
if {n for _, n in report} != {NEWEND}: raise SystemExit(f'track lengths differ: {report} vs {NEWEND}')
out.tracks.markers.clear()
for mk in tl.tracks.markers:
    f = fwd(fr(mk.marked_range.start_time))
    if f is None: continue
    nm = mk.deepcopy(); nm.marked_range = otio.opentime.TimeRange(otio.opentime.RationalTime(f, FPS), mk.marked_range.duration)
    out.tracks.markers.append(nm)
print(json.dumps(dict(END=END, NEWEND=NEWEND, delta=NEWEND - END, pauses={k: tc(v) for k, v in pause_new.items()},
                      src_blocks={tc(k): tc(v) for k, v in src_new.items()}), ensure_ascii=False))
json.dump(dict(layout=[(p0, L, list(sg)) for p0, L, sg in layout], END=END, NEWEND=NEWEND),
          open(os.path.join(S, 'v38_map.json'), 'w'), indent=1)
if MODE != 'apply': raise SystemExit
path_out = os.path.join(S, 'splice_out.otio'); otio.adapters.write_to_file(out, path_out)
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
def state(i, f):
    x = api_at('video', i, f); return (x.GetClipColor(), x.GetClipEnabled()) if x else None
nxt_state = state(2, 33158); prv_state = state(2, 32302)
for i in range(1, created.GetTrackCount('video') + 1):
    for x in created.GetItemListInTrack('video', i):
        if not x.GetMediaPoolItem(): continue
        name = created.GetTrackName('video', i)
        if name == "Plans d'appui": x.SetClipColor('Green'); continue
        if i >= 4: x.SetClipColor('Purple'); continue
        if i == 3: continue
        nf = int(x.GetStart() - cst); sg, rel = back(nf)
        if sg[0] == 'tl': hit = state(i, sg[1] + rel)
        elif sg[0] == 'pause': hit = state(i, sg[1] - 1) if rel < HEAD else state(i, sg[4] if len(sg) > 4 else sg[1])
        elif sg[1] in (27989, 43975): hit = state(i, sg[1])
        else:
            col = state(i, 32303)[0]
            en = (not prv_state[1]) if rel < 376 else (not nxt_state[1])
            hit = (col, en if i == 2 else True)
        if hit:
            if hit[0]: x.SetClipColor(hit[0])
            x.SetClipEnabled(hit[1])
p.SetCurrentTimeline(created)
print('cree', created.GetName(), int(created.GetEndFrame() - cst), 'attendu', NEWEND)

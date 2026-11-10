"""Generic edit pass: from a source timeline, apply a list of edits in source frames, each {P, Q, D, panels}:
remove [P, Q) on all non-graphic tracks and insert a pause of D frames there (D=0: plain cut). Panels named in
`panels` (Motion items) are placed at the start of their pause; every other graphic follows its content.
Usage: edit_pass.py <plan.json>. The plan holds SRC, NEW and edits. The source timeline is only exported."""
import json, os, sys, time
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
import opentimelineio as otio

S = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) == 'exemples_lili' else os.path.dirname(os.path.abspath(__file__)), 'work'); os.makedirs(S, exist_ok=True)
plan = json.load(open(sys.argv[1], encoding='utf8'))
SRC, NEW, FPS = plan['SRC'], plan['NEW'], 24
EDITS = sorted(plan['edits'], key=lambda e: e['P'])
for a, b in zip(EDITS, EDITS[1:]):
    if a['Q'] > b['P']: raise SystemExit(f'overlapping edits at {a["P"]} / {b["P"]}')
resolve, p = r.project()
ts = {t.GetName(): t for t in r.timelines(p)}
if NEW in ts: raise SystemExit(f'{NEW} exists.')
src = ts[SRC]; st = src.GetStartFrame()
path_in = os.path.join(S, 'pass_in.otio')
if not src.Export(path_in, resolve.EXPORT_OTIO): raise SystemExit('export failed')
tl = otio.adapters.read_from_file(path_in)
def fr(t):
    v = t.rescaled_to(FPS).value; assert abs(v - round(v)) < 1e-6; return int(round(v))
END = fr(tl.duration())
kept, pos, prev = [], 0, 0
for e in EDITS:
    kept.append(dict(a=prev, b=e['P'], new=pos)); pos += e['P'] - prev
    e['new'] = pos; pos += e['D']; prev = e['Q']
kept.append(dict(a=prev, b=END, new=pos)); NEWEND = pos + END - prev
def fwd(f):
    for k in kept:
        if k['a'] <= f < k['b'] or (f == END and k['b'] == END): return k['new'] + f - k['a']
    e = next(e for e in EDITS if e['P'] <= f < e['Q']); return e['new'] + e['D']
def back(n):
    for k in kept:
        if k['new'] <= n < k['new'] + (k['b'] - k['a']): return k['a'] + n - k['new']
panel_of = {name: e for e in EDITS for name in e.get('panels', [])}
def gap(n): return otio.schema.Gap(duration=otio.opentime.RationalTime(n, FPS))
def piece(item, a, b, cursor):
    pc = item.deepcopy(); s0 = item.trimmed_range().start_time
    pc.source_range = otio.opentime.TimeRange(s0 + otio.opentime.RationalTime(a - cursor, s0.rate),
                                              otio.opentime.RationalTime(b - a, s0.rate))
    ps = pc.source_range.start_time; pe = ps + pc.source_range.duration
    mks = list(pc.markers); pc.markers.clear()
    for mk in mks:
        if ps <= mk.marked_range.start_time < pe:
            nm = mk.deepcopy(); nm.marked_range = otio.opentime.TimeRange(
                mk.marked_range.start_time, min(mk.marked_range.duration, pe - mk.marked_range.start_time))
            pc.markers.append(nm)
    return pc
bounds = sorted({x for e in EDITS for x in (e['P'], e['Q'])})
out = tl.deepcopy(); out.name = NEW
link_ids, nxt = {}, 1
for orig, target in zip(tl.tracks, out.tracks):
    target.clear()
    if orig.kind == 'Video' and not orig.name.startswith('Camera'):
        placed, cursor, chain_end = [], 0, {}
        for item in orig:
            if isinstance(item, otio.schema.Transition): continue
            L = fr(item.duration())
            if isinstance(item, otio.schema.Clip):
                e = panel_of.get(item.name)
                if e is not None:
                    off = e.setdefault('_off', 0); new = e['new'] + off; e['_off'] = off + L
                else:
                    new = fwd(cursor)
                placed.append((new, L, item))
            cursor += L
        placed.sort(key=lambda x: x[0]); c = 0
        for new, L, item in placed:
            if new < c: raise SystemExit(f'{orig.name} overlap at {item.name} ({new} < {c})')
            if new > c: target.append(gap(new - c))
            target.append(item.deepcopy()); c = new + L
        if c < NEWEND: target.append(gap(NEWEND - c))
        for e in EDITS: e.pop('_off', None)
        continue
    pieces, cursor = [], 0
    for item in orig:
        if isinstance(item, otio.schema.Transition): raise SystemExit(f'transition on {orig.name}')
        L = fr(item.duration())
        cuts = [cursor] + [x for x in bounds if cursor < x < cursor + L] + [cursor + L]
        for a, b in zip(cuts, cuts[1:]):
            if isinstance(item, otio.schema.Gap): pieces.append((a, b, gap(b - a))); continue
            pc = piece(item, a, b, cursor) if len(cuts) > 2 else item.deepcopy()
            meta = pc.metadata.get('Resolve_OTIO', {})
            if len(cuts) > 2 and meta.get('Link Group ID') is not None:
                key = (meta['Link Group ID'], a, b)
                if key not in link_ids: link_ids[key] = 300000 + nxt; nxt += 1
                meta['Link Group ID'] = link_ids[key]
            pieces.append((a, b, pc))
        cursor += L
    for k, rng in enumerate(kept):
        if k > 0 and EDITS[k - 1]['D']: target.append(gap(EDITS[k - 1]['D']))
        for a, b, pc in pieces:
            if rng['a'] <= a and b <= rng['b']: target.append(pc)
lens = {t.name: fr(t.duration()) for t in out.tracks}
if set(lens.values()) != {NEWEND}: raise SystemExit(f'track lengths differ: {lens}')
out.tracks.markers.clear()
for mk in tl.tracks.markers:
    nm = mk.deepcopy(); nm.marked_range = otio.opentime.TimeRange(
        otio.opentime.RationalTime(fwd(fr(mk.marked_range.start_time)), FPS), mk.marked_range.duration)
    out.tracks.markers.append(nm)
path_out = os.path.join(S, 'pass_out.otio'); otio.adapters.write_to_file(out, path_out)
used = {x.GetMediaPoolItem().GetUniqueId() for k in ('video', 'audio') for i in range(1, src.GetTrackCount(k) + 1)
        for x in (src.GetItemListInTrack(k, i) or []) if x.GetMediaPoolItem()}
folders = []
def walk(f):
    if any(c_.GetUniqueId() in used for c_ in (f.GetClipList() or [])): folders.append(f)
    for s_ in f.GetSubFolderList() or []: walk(s_)
walk(p.GetMediaPool().GetRootFolder())
created = p.GetMediaPool().ImportTimelineFromFile(path_out, {'timelineName': NEW, 'importSourceClips': False,
                                                            'sourceClipsFolders': folders})
if not created: raise SystemExit('import failed')
time.sleep(1)
if created.GetStartTimecode() != src.GetStartTimecode(): created.SetStartTimecode(src.GetStartTimecode())
cst = created.GetStartFrame()
new_ids = {x.GetMediaPoolItem().GetUniqueId() for k in ('video', 'audio') for i in range(1, created.GetTrackCount(k) + 1)
           for x in (created.GetItemListInTrack(k, i) or []) if x.GetMediaPoolItem()}
if not new_ids <= used: raise SystemExit('import created new media; inspect.')
for i in range(1, created.GetTrackCount('video') + 1):
    srcs = [(int(x.GetStart() - st), int(x.GetEnd() - st), x.GetClipColor(), x.GetClipEnabled())
            for x in src.GetItemListInTrack('video', i) if x.GetMediaPoolItem()]
    for x in created.GetItemListInTrack('video', i):
        if not x.GetMediaPoolItem(): continue
        if created.GetTrackName('video', i) == "Plans d'appui": x.SetClipColor('Green'); continue
        if i >= 4: x.SetClipColor('Purple'); continue
        f0 = back(int(x.GetStart() - cst))
        hit = next(((c_, en) for sa, sb, c_, en in srcs if f0 is not None and sa <= f0 < sb), None)
        if hit:
            if hit[0]: x.SetClipColor(hit[0])
            x.SetClipEnabled(hit[1])
json.dump(dict(kept=kept, edits=EDITS, END=END, NEWEND=NEWEND), open(os.path.join(S, plan.get('map', 'pass_map.json')), 'w'), indent=1)
print(json.dumps(dict(name=created.GetName(), frames=int(created.GetEndFrame() - cst), expected=NEWEND,
                      delta_s=round((NEWEND - END) / FPS, 1), edits=len(EDITS)), ensure_ascii=False))

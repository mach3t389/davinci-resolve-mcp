"""Finishing pass on V3.0 (whole In-Out range), result V3.1. plan|apply.
 - camera choice: strict alternation, a switch at every real jump cut when possible, shots 2.5-10 s, video-only splits
 - remnants < 12 frames touching a pause removed; camera VIDEO runs 12 frames under panel fade-ins and 20 under fade-outs
 - chained panels: the second one goes on the track above and fades in over the first (pause shortened accordingly)
 - 'hold' pause (2-3 s en suspens) filled with the neighbouring take
 - audio: 3-frame fades at every pause edge, voice isolation on Audio 1
 - b-roll overlays (Plans d'appui) end in a silence of the speech"""
import json, os, subprocess, sys, time, array, math
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
import opentimelineio as otio

S = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) == 'exemples_lili' else os.path.dirname(os.path.abspath(__file__)), 'work'); os.makedirs(S, exist_ok=True)
SRC, NEW = 'Lili V3.0 - correctifs video complete', 'Lili V3.1 - finition video complete'
FPS, HEAD, TAIL, SLIVER, XF, AFADE = 24, 12, 20, 12, 12, 3
CAM = {1: (3 * FPS, 10 * FPS, 8 * FPS), 2: (2 * FPS, 6 * FPS, int(3.5 * FPS))}
ISOLATION = 70
mode = sys.argv[1]
resolve, p = r.project()
ts = {t.GetName(): t for t in r.timelines(p)}
src = ts[SRC]; st = src.GetStartFrame()
OUT = [int(float(k)) for k, v in src.GetMarkers().items() if v['name'] == 'Out'][0]
def tc(f): return f'{int(f) // 1440:02d}:{(int(f) % 1440) / 24:05.2f}'

# ---------- words in V3.0 frames ----------
m30 = json.load(open(os.path.join(S, 'v30_map.json')))
def fwd30(f):
    for k in m30['kept']:
        if k['a'] <= f < k['b']: return k['new'] + f - k['a']
words = []
for w in json.load(open(os.path.join(S, 'full_v29_transcript.json'), encoding='utf8'))['words']:
    a, b = fwd30(int(round(w['f_start']))), fwd30(int(round(w['f_end'])))
    if a is not None and b is not None: words.append((a, max(b, a + 1), w['word']))
words.sort()
pause_pts = set()
for (a1, b1, w1), (a2, b2, w2) in zip(words, words[1:]):
    if a2 - a1 > 6 and (w1.rstrip()[-1:] in '.,?!;:' or a2 - b1 >= 2): pause_pts.add(a2 - 2)

# ---------- structure of V3.0 ----------
A1 = [x for x in src.GetItemListInTrack('audio', 1)]
gaps = []                                        # pauses measured on Audio 1 (video may already run under the fades)
for a, b in zip(A1, A1[1:]):
    e, s = int(a.GetEnd() - st), int(b.GetStart() - st)
    if s - e >= 40 and e < OUT: gaps.append([e, s])
jumps = set()
for a, b in zip(A1, A1[1:]):
    if int(a.GetEnd() - st) == int(b.GetStart() - st) and (a.GetMediaPoolItem().GetUniqueId() != b.GetMediaPoolItem().GetUniqueId()
            or b.GetLeftOffset() != a.GetLeftOffset() + a.GetDuration()):
        jumps.add(int(b.GetStart() - st))
motion = sorted([(int(x.GetStart() - st), int(x.GetEnd() - st), x.GetName()) for x in src.GetItemListInTrack('video', 4)
                 if x.GetMediaPoolItem()])
def alpha_fadeout(name):
    m = next(x for x in src.GetItemListInTrack('video', 4) if x.GetMediaPoolItem() and x.GetName() == name).GetMediaPoolItem()
    r_ = subprocess.run(['ffmpeg', '-v', 'error', '-i', m.GetClipProperty('File Path'), '-vf',
                         'alphaextract,scale=32:18,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-', '-f', 'null', '-'],
                        capture_output=True, text=True, encoding='utf8', errors='replace')
    v = [float(l.split('=')[1]) for l in r_.stdout.splitlines() if 'YAVG=' in l]
    mx = max(v); return next(i for i, x in enumerate(reversed(v)) if x > 0.98 * mx)
chains = []                                      # (first, second) panels touching each other inside a pause
for a, b in zip(motion, motion[1:]):
    if a[1] == b[0] and any(g[0] - 30 <= a[0] < g[1] for g in gaps):
        first_item = next(x for x in src.GetItemListInTrack('video', 4) if x.GetMediaPoolItem() and x.GetName() == a[2])
        fo = alpha_fadeout(a[2]) if first_item.GetDuration() >= 100 else 0
        lo = int(first_item.GetLeftOffset()); full = int(first_item.GetMediaPoolItem().GetClipProperty('Frames'))
        fo = fo if lo + first_item.GetDuration() >= full - 1 else 0     # a trimmed first panel has no fade-out left
        chains.append(dict(first=a, second=b, OV=fo + XF))

# ---------- camera plan per stretch (between pauses) ----------
stretches, c = [], 0
for g0, g1 in gaps:
    stretches.append((c, g0)); c = g1
stretches.append((c, OUT))
def solve(s, e, first):
    if e - s < CAM[2][0] * 2: return [dict(a=s, b=e, cam=1)]
    cands = sorted({s, e} | {j for j in jumps if s < j < e} | {q for q in pause_pts if s < q < e and all(abs(q - j) > 12 for j in jumps)})
    def cost(x0, x1, cam, relax):
        L = x1 - x0; lo, hi, ideal = CAM[cam]
        if L < (lo if not relax else 36) or (L > hi and not relax): return None
        cst = ((L - ideal) / FPS) ** 2 * 0.3 + 20 * sum(1 for j in jumps if x0 < j < x1)
        if cam == 2: cst += 0.8 * max(0, L - ideal) / FPS
        if L > hi: cst += 40 + 5 * (L - hi) / FPS
        if L < lo: cst += 60
        if x1 != e and x1 not in jumps: cst += 1
        return cst
    for relax in (False, True):
        best = {(0, 3 - first): (0.0, None)} if first else {(0, 1): (0.5, None), (0, 2): (0.0, None)}
        n = len(cands)
        for i in range(1, n):
            for cam in (1, 2):
                opts = []
                for j in range(i - 1, -1, -1):
                    if cands[i] - cands[j] > (40 * FPS if relax else CAM[cam][1]): break
                    pv = best.get((j, 3 - cam))
                    if pv is None: continue
                    cc = cost(cands[j], cands[i], cam, relax)
                    if cc is not None: opts.append((pv[0] + cc, (j, 3 - cam)))
                if opts: best[(i, cam)] = min(opts)
        ends = [k for k in best if k[0] == n - 1]
        if ends:
            k = min(ends, key=lambda q: best[q][0]); shots = []
            while best[k][1] is not None:
                j, pc = best[k][1]; shots.append(dict(a=cands[j], b=cands[k[0]], cam=k[1])); k = (j, pc)
            return shots[::-1]
    return [dict(a=s, b=e, cam=1)]
shots = []
for i, (s, e) in enumerate(stretches):
    if e > s: shots += solve(s, e, 1 if i == 0 else None)
tot = sum(x['b'] - x['a'] for x in shots); c2 = sum(x['b'] - x['a'] for x in shots if x['cam'] == 2)
vis_jumps = [j for j in jumps if j < OUT and not any(x['a'] == j for x in shots)]
long_ = [(tc(x['a']), round((x['b'] - x['a']) / FPS, 1)) for x in shots if x['b'] - x['a'] > 10 * FPS]
print(json.dumps(dict(out=tc(OUT), pauses=len(gaps), real_cuts=len(jumps), shots=len(shots), cam2=round(c2 / tot, 3),
                      jump_cuts_same_camera=len(vis_jumps), shots_over_10s=long_,
                      shortest=min(x['b'] - x['a'] for x in shots), chains=[(c_['first'][2][:6], c_['second'][2][:6], c_['OV']) for c_ in chains])))
if vis_jumps: print('jump cuts restes sur la meme camera:', [tc(j) for j in sorted(vis_jumps)])
json.dump(dict(shots=shots, gaps=gaps, chains=chains), open(os.path.join(S, 'finish_plan.json'), 'w'), indent=1)
if mode != 'apply': sys.exit()

# ---------- OTIO rebuild ----------
if NEW in ts: raise SystemExit('exists')
path_in = os.path.join(S, 'fin_in.otio')
if not src.Export(path_in, resolve.EXPORT_OTIO): raise SystemExit('export failed')
tl = otio.adapters.read_from_file(path_in)
def fr(t):
    v = t.rescaled_to(FPS).value; assert abs(v - round(v)) < 1e-6; return int(round(v))
END = fr(tl.duration())
SHR = sorted((next(g[1] for g in gaps if g[0] - 30 <= c_['first'][0] < g[1]), c_['OV']) for c_ in chains)
def f2(f): return f - sum(ov for pe, ov in SHR if pe <= f)      # V3.0 frame -> V3.1 frame (after chain shrink)
NEWEND = f2(END)
edges = {x for g in gaps for x in g} | {0, OUT}
bounds = sorted(({x['a'] for x in shots} | {x['b'] for x in shots}) - edges)
def placements(track):
    pl, c_ = [], 0
    for it in track:
        L = fr(it.duration())
        if isinstance(it, otio.schema.Clip): pl.append([c_, c_ + L, it])
        c_ += L
    return pl
def trim(clip, h, t_):
    sr = clip.source_range; rate = sr.start_time.rate
    clip.source_range = otio.opentime.TimeRange(sr.start_time + otio.opentime.RationalTime(h, rate),
                                                sr.duration - otio.opentime.RationalTime(h + t_, rate))
def room(clip):
    sr = clip.source_range; ar = clip.media_reference.available_range; rate = sr.start_time.rate
    if ar is None: return 0, 0
    return (int((sr.start_time - ar.start_time).rescaled_to(rate).value), int((ar.end_time_exclusive() - sr.end_time_exclusive()).rescaled_to(rate).value))
def pcm_db(path, s_, d_):
    r_ = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{max(s_, 0):.5f}', '-t', f'{d_:.5f}', '-i', path, '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], capture_output=True)
    a = array.array('h'); a.frombytes(r_.stdout[:len(r_.stdout) // 2 * 2]); x = [v / 32768 for v in a]
    return 20 * math.log10(math.sqrt(sum(v * v for v in x) / max(len(x), 1)) + 1e-9)
out = tl.deepcopy(); out.name = NEW
hold_gaps = [g for g in gaps if g[1] - g[0] < 100]
hold_choice = {}
report = []
for track in out.tracks:
    pl = placements(track)
    for it in [i for i in track if isinstance(i, otio.schema.Transition)]: track.remove(it)
    camera = track.name.startswith('Camera') or track.name.startswith('Audio')
    if camera:
        if track.kind == 'Video':                                   # camera splits: video only
            npl = []
            for a, b, it in pl:
                cuts = [a] + [x for x in bounds if a < x < b] + [b]
                for x, y in zip(cuts, cuts[1:]):
                    pc = it.deepcopy() if len(cuts) > 2 else it
                    if len(cuts) > 2:
                        trim(pc, x - a, b - y)
                        meta = pc.metadata.get('Resolve_OTIO', {})
                        meta.pop('Link Group ID', None)
                        ps = pc.source_range.start_time; pe = ps + pc.source_range.duration
                        mks = list(pc.markers); pc.markers.clear()
                        for mk in mks:
                            if ps <= mk.marked_range.start_time < pe: pc.markers.append(mk)
                    npl.append([x, y, pc])
            pl = npl
        # shift for chain shrink: a clip moves when it ends after a shortened pause (keeps its length)
        for q in pl:
            sh = sum(ov for pe, ov in SHR if q[1] > pe); q[0] -= sh; q[1] -= sh
        for g0, g1 in gaps:
            P0, P1 = f2(g0), f2(g1 - 1) + 1 if g1 - 1 >= g0 else f2(g1)
            P1 = f2(g1) if not any(pe == g1 for pe, _ in SHR) else f2(g1)
            # remnants touching the pause: picture only, up to 1 s, and only when nobody speaks in it
            def wordless(q): return not any(q[0] - 1 <= f2(wa) < q[1] for wa, wb, _ in words)
            while track.kind == 'Video':
                pre = max((q for q in pl if q[0] < P0), key=lambda q: q[1], default=None)
                if pre and pre[1] - pre[0] < 24 and pre[1] >= P0 - 24 and pre[0] < P0 and wordless(pre): pl.remove(pre); report.append(('reste', track.name, tc(pre[0]))); continue
                break
            while track.kind == 'Video':
                post = min((q for q in pl if q[1] > P1), key=lambda q: q[0], default=None)
                if post and post[1] - post[0] < 24 and post[0] <= P1 + 24 and wordless(post): pl.remove(post); report.append(('reste', track.name, tc(post[0]))); continue
                break
            pre = max((q for q in pl if q[0] < P0), key=lambda q: q[1], default=None)
            post = min((q for q in pl if q[1] > P1), key=lambda q: q[0], default=None)
            if pre is None or post is None: continue
            if [g0, g1] in hold_gaps:                                   # fill the hold with a silent neighbour
                L = P1 - pre[1]
                if (g0, g1) not in hold_choice:
                    path = pre[2].media_reference.target_url.replace('file:///', '').replace('file://', '')
                    rate = pre[2].source_range.start_time.rate
                    after_db = pcm_db(path, pre[2].source_range.end_time_exclusive().to_seconds(), L / rate)
                    path2 = post[2].media_reference.target_url.replace('file:///', '').replace('file://', '')
                    before_db = pcm_db(path2, post[2].source_range.start_time.to_seconds() - L / rate, L / rate)
                    hold_choice[(g0, g1)] = ('pre' if after_db <= before_db else 'post', round(after_db, 1), round(before_db, 1))
                side = hold_choice[(g0, g1)][0]
                if side == 'pre' and room(pre[2])[1] >= L:
                    sr = pre[2].source_range; pre[2].source_range = otio.opentime.TimeRange(sr.start_time, sr.duration + otio.opentime.RationalTime(L, sr.start_time.rate)); pre[1] += L
                elif room(post[2])[0] >= L:
                    sr = post[2].source_range; pre_ = otio.opentime.RationalTime(L, sr.start_time.rate)
                    post[2].source_range = otio.opentime.TimeRange(sr.start_time - pre_, sr.duration + pre_); post[0] -= L
                continue
            if track.kind != 'Video': continue
            e1 = max(0, min(P0 + HEAD - pre[1], room(pre[2])[1])); e2 = max(0, min(post[0] - (P1 - TAIL), room(post[2])[0]))
            if e1 > 0:
                sr = pre[2].source_range; pre[2].source_range = otio.opentime.TimeRange(sr.start_time, sr.duration + otio.opentime.RationalTime(e1, sr.start_time.rate)); pre[1] += e1
            if e2 > 0:
                sr = post[2].source_range; d_ = otio.opentime.RationalTime(e2, sr.start_time.rate)
                post[2].source_range = otio.opentime.TimeRange(sr.start_time - d_, sr.duration + d_); post[0] -= e2
    elif track.name in ('Motion', 'Video 5'):
        moved = {c_['second'][2]: c_ for c_ in chains}
        npl = []
        for a, b, it in pl:
            if track.name == 'Motion' and it.name in moved:
                c_ = moved[it.name]; na = f2(c_['first'][1]) - c_['OV']
                c_['_target'] = (na, na + (b - a), it)                  # goes to the track above
                continue
            npl.append([f2(a), f2(a) + (b - a), it])
        if track.name == 'Video 5':
            for c_ in chains: npl.append(list(c_['_target']))
        pl = npl
    else:
        for q in pl: q[0], q[1] = f2(q[0]), f2(q[0]) + (q[1] - q[0])
    pl.sort(key=lambda q: q[0]); track.clear(); c_ = 0
    for a, b, it in pl:
        if a < c_: raise SystemExit(f'overlap on {track.name} at {tc(a)}')
        if a > c_: track.append(otio.schema.Gap(duration=otio.opentime.RationalTime(a - c_, FPS)))
        track.append(it); c_ = b
    if c_ < NEWEND: track.append(otio.schema.Gap(duration=otio.opentime.RationalTime(NEWEND - c_, FPS)))
lens = {t.name: fr(t.duration()) for t in out.tracks}
if set(lens.values()) != {NEWEND}: raise SystemExit(f'lengths {lens}')
out.tracks.markers.clear()
for mk in tl.tracks.markers:
    nm = mk.deepcopy(); nm.marked_range = otio.opentime.TimeRange(otio.opentime.RationalTime(f2(fr(mk.marked_range.start_time)), FPS), mk.marked_range.duration)
    out.tracks.markers.append(nm)
path_out = os.path.join(S, 'fin_out.otio'); otio.adapters.write_to_file(out, path_out)
used = {x.GetMediaPoolItem().GetUniqueId() for k in ('video', 'audio') for i in range(1, src.GetTrackCount(k) + 1)
        for x in (src.GetItemListInTrack(k, i) or []) if x.GetMediaPoolItem()}
folders = []
def walk(f):
    if any(c__.GetUniqueId() in used for c__ in (f.GetClipList() or [])): folders.append(f)
    for s_ in f.GetSubFolderList() or []: walk(s_)
walk(p.GetMediaPool().GetRootFolder())
created = p.GetMediaPool().ImportTimelineFromFile(path_out, {'timelineName': NEW, 'importSourceClips': False, 'sourceClipsFolders': folders})
if not created: raise SystemExit('import failed')
time.sleep(1)
if created.GetStartTimecode() != src.GetStartTimecode(): created.SetStartTimecode(src.GetStartTimecode())
cst = created.GetStartFrame()
new_ids = {x.GetMediaPoolItem().GetUniqueId() for k in ('video', 'audio') for i in range(1, created.GetTrackCount(k) + 1)
           for x in (created.GetItemListInTrack(k, i) or []) if x.GetMediaPoolItem()}
if not new_ids <= used: raise SystemExit('import created new media; inspect.')
p.SetCurrentTimeline(created); time.sleep(2)
OUT2 = f2(OUT)
shots2 = [dict(a=f2(x['a']), b=f2(x['b']), cam=x['cam']) for x in shots]
srcs = {i: [(int(x.GetStart() - st), int(x.GetEnd() - st), x.GetClipColor(), x.GetClipEnabled()) for x in src.GetItemListInTrack('video', i) if x.GetMediaPoolItem()] for i in (1, 2)}
def inv(n):
    for f in range(n, n + sum(ov for _, ov in SHR) + 1):
        if f2(f) == n: return f
for i in range(1, created.GetTrackCount('video') + 1):
    name = created.GetTrackName('video', i)
    for x in created.GetItemListInTrack('video', i):
        if not x.GetMediaPoolItem(): continue
        if name == "Plans d'appui": x.SetClipColor('Green'); continue
        if i >= 4: x.SetClipColor('Purple'); continue
        a = int(x.GetStart() - cst); mid = (a + int(x.GetEnd() - cst)) // 2
        f0 = inv(mid); col = next((c__ for sa, sb, c__, _ in srcs[i] if f0 is not None and sa <= f0 < sb), '')
        if col: x.SetClipColor(col)
        if i == 2:
            sh = next((s_ for s_ in shots2 if s_['a'] <= mid < s_['b']), None)
            if sh: x.SetClipEnabled(sh['cam'] == 1)
            else:
                en = next((e_ for sa, sb, _, e_ in srcs[2] if f0 is not None and sa <= f0 < sb), True); x.SetClipEnabled(en)
# transitions: fade-in on a panel that starts over picture; the second of a chain fades in over the first
firsts = {c_['first'][2] for c_ in chains}; seconds = {c_['second'][2] for c_ in chains}
fades = []
for i in (4, 5):
    for x in created.GetItemListInTrack('video', i):
        if not x.GetMediaPoolItem(): continue
        n = x.GetName()
        if n.startswith('Lili') and (n in seconds or any(g[0] == inv(int(x.GetStart() - cst)) for g in gaps)):
            fades.append((n[:6], bool(x.AddTransition({'type': 'Cross Dissolve', 'category': 'simple', 'position': 'start', 'alignment': 'right', 'duration': XF}))))
# audio fades at pause edges
afade = 0
for i in (1, 2):
    its = created.GetItemListInTrack('audio', i)
    for a, b in zip(its, its[1:]):
        if b.GetStart() > a.GetEnd() and a.GetStart() - cst < OUT2:
            fa = a.GetFades() or {}; fb = b.GetFades() or {}
            a.SetFades({'FadeIn': fa.get('FadeIn', 0), 'FadeOut': AFADE}); b.SetFades({'FadeIn': AFADE, 'FadeOut': fb.get('FadeOut', 0)}); afade += 1
iso = created.SetVoiceIsolationState(1, {'isEnabled': True, 'amount': ISOLATION})
# b-roll overlays end in a silence: move the end to the nearest pause in speech (trim or extend within the media)
words2 = [(f2(a), f2(b)) for a, b, _ in words]
def in_word(f): return any(a + 1 < f < b - 1 for a, b in words2)
broll = []
pool = p.GetMediaPool()
v3 = next(i for i in range(1, created.GetTrackCount('video') + 1) if created.GetTrackName('video', i) == "Plans d'appui")
for x in list(created.GetItemListInTrack('video', v3)):
    a, b = int(x.GetStart() - cst), int(x.GetEnd() - cst)
    if a >= OUT2 or not in_word(b): continue
    nxt_start = min((int(y.GetStart() - cst) for y in created.GetItemListInTrack('video', v3) if y.GetStart() - cst > a), default=10 ** 9)
    gaps_w = sorted({w_b + 1 for (w_a, w_b), (n_a, n_b) in zip(words2, words2[1:]) if n_a - w_b >= 2} | {n_a - 2 for (w_a, w_b), (n_a, n_b) in zip(words2, words2[1:]) if n_a - w_b >= 2})
    full = int(x.GetMediaPoolItem().GetClipProperty('Frames')); lo = int(x.GetLeftOffset())
    opts = [g for g in gaps_w if abs(g - b) <= 36 and g - a >= 48 and lo + (g - a) <= full and g <= nxt_start]
    if not opts: broll.append((x.GetName()[:24], tc(b), 'pas de silence proche')); continue
    nb = min(opts, key=lambda g: abs(g - b)); mpi = x.GetMediaPoolItem(); props = dict(x.GetProperty() or {})
    created.DeleteClips([x], False)
    made = pool.AppendToTimeline([{'mediaPoolItem': mpi, 'startFrame': lo, 'endFrame': lo + (nb - a), 'trackIndex': v3, 'recordFrame': cst + a, 'mediaType': 1}])
    if made:
        for k, v in props.items():
            try: made[0].SetProperty(k, v)
            except Exception: pass
        made[0].SetClipColor('Green')
    broll.append((mpi.GetName()[:24], tc(b), '->', tc(nb)))
rs = []
for x in [y for y in created.GetItemListInTrack('video', 2) if y.GetStart() - cst < OUT2]:
    a, b = int(x.GetStart() - cst), int(x.GetEnd() - cst); cam = 1 if x.GetClipEnabled() else 2
    if rs and rs[-1][2] == cam and rs[-1][1] == a: rs[-1][1] = b
    else: rs.append([a, b, cam])
print(json.dumps(dict(name=created.GetName(), frames=int(created.GetEndFrame() - cst), expected=NEWEND, removed_remnants=len(report),
                      hold=list(hold_choice.values()), fades=fades, audio_fades=afade, voice_isolation=iso, broll=broll,
                      shortest_shot=min(b - a for a, b, _ in rs), shots=len(rs),
                      clip_markers=sum(len(x.GetMarkers() or {}) for i in (1, 2) for x in created.GetItemListInTrack('video', i))), ensure_ascii=False))

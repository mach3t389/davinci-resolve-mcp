"""V2.9 from V2.8: real cuts (all camera tracks cut at the same frame) whose outgoing audio still has sound are
extended up to the first silent frame (max 8 frames, waveform of A1), with ripple on every track. plan|apply."""
import json, os, subprocess, sys, time, array, math
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
import opentimelineio as otio

S = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) == 'exemples_lili' else os.path.dirname(os.path.abspath(__file__)), 'work'); os.makedirs(S, exist_ok=True)
SRC, NEW = 'Lili V3.1 - finition video complete', 'Lili V3.2 - coupes sur le silence'
FPS, MAXK, MIN_SHOT = 24, 8, 60
FADE_IN = ['Lili01', 'Lili03', 'Lili04', 'Lili05', 'Lili06', 'Lili07']
mode = sys.argv[1]
resolve, p = r.project()
ts = {t.GetName(): t for t in r.timelines(p)}
src = ts[SRC]; st = src.GetStartFrame()
OUT = [int(float(k)) for k, v in src.GetMarkers().items() if v['name'] == 'Out'][0]

def pcm(path, start_s, dur_s):
    r_ = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{max(start_s, 0):.5f}', '-t', f'{dur_s:.5f}', '-i', path,
                         '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], capture_output=True)
    a = array.array('h'); a.frombytes(r_.stdout[:len(r_.stdout) // 2 * 2]); return [v / 32768 for v in a]
def db(x): return 20 * math.log10(math.sqrt(sum(v * v for v in x) / max(len(x), 1)) + 1e-9)

A = [x for x in src.GetItemListInTrack('audio', 1) if x.GetStart() - st < OUT]
cuts_all = {k: {int(x.GetStart() - st) for x in src.GetItemListInTrack(k2, i)} for k, (k2, i) in
            {'V1': ('video', 1), 'V2': ('video', 2), 'A2': ('audio', 2)}.items()}
silence = []
plan = []
for a, b in zip(A, A[1:]):
    c = int(b.GetStart() - st)
    if int(a.GetEnd() - st) != c: continue
    if not all(c in s_ for s_ in cuts_all.values()): continue        # only real cuts present on every camera track
    m = a.GetMediaPoolItem(); fps = float(m.GetClipProperty('FPS')); path = m.GetClipProperty('File Path')
    end_s = (a.GetLeftOffset() + a.GetDuration()) / fps
    tail = db(pcm(path, end_s - 2 / fps, 2 / fps))
    floor = db(pcm(path, end_s - 4, 4))                              # context, used to estimate the room tone
    frames = [db(pcm(path, end_s + j / fps, 1 / fps)) for j in range(MAXK + 1)]
    plan.append(dict(cut=c, tail_db=round(tail, 1), after=[round(v, 1) for v in frames], path=path, end_s=end_s))
# room tone: the quietest frames seen around cuts
room = sorted(v for pl in plan for v in pl['after'])[len(plan)]
QUIET = room + 8
def tc(f): return f'{f // 1440:02d}:{(f % 1440) / 24:05.2f}'
todo = []
for pl in plan:
    if pl['tail_db'] <= QUIET: continue
    k = next((j for j, v in enumerate(pl['after']) if v <= QUIET), None)
    pl['k'] = k
    if k: todo.append((pl['cut'], k))
    print(f"{tc(pl['cut'])} fin {pl['tail_db']} dB -> {'+' + str(k) + ' images' if k else ('deja au silence' if k == 0 else 'pas de silence en 8 images : laisse')}")
print(json.dumps(dict(real_cuts=len(plan), room_db=round(room, 1), quiet_db=round(QUIET, 1), to_extend=len(todo),
                      added_frames=sum(k for _, k in todo))))
json.dump(dict(todo=todo, plan=plan), open(os.path.join(S, 'v29_plan.json'), 'w'), indent=1)
if mode != 'apply': sys.exit()

if NEW in ts: raise SystemExit('V3.2 exists.')
TRANS = {}
for i in (4, 5):
    its = src.GetItemListInTrack('video', i)
    starts = {int(x.GetStart()) for x in its if not x.GetMediaPoolItem()}
    TRANS[i] = {x.GetName() for x in its if x.GetMediaPoolItem() and int(x.GetStart()) in starts}
path_in = os.path.join(S, 'v28_now.otio')
if not src.Export(path_in, resolve.EXPORT_OTIO): raise SystemExit('export failed')
tl = otio.adapters.read_from_file(path_in)
def fr(t):
    v = t.rescaled_to(FPS).value; assert abs(v - round(v)) < 1e-6; return int(round(v))
END = fr(tl.duration()); ADD = sum(k for _, k in todo); NEWEND = END + ADD
todo.sort()
def shift(f):            # frames added before position f (a cut at c moves everything starting at or after c)
    return sum(k for c, k in todo if c <= f)
def back(n):
    for f in range(n, n - ADD - 1, -1):
        if f + shift(f) == n: return f
    return None
out = tl.deepcopy(); out.name = NEW; ext = 0
for track in out.tracks:
    if track.name == 'Motion':
        for it in [i for i in track if isinstance(i, otio.schema.Transition)]: track.remove(it)
    pl, c = [], 0
    for it in track:
        L = fr(it.duration())
        if isinstance(it, otio.schema.Clip): pl.append([c, c + L, it])
        c += L
    camera = track.name.startswith('Camera') or track.name.startswith('Audio')
    new = []
    for a, b, it in pl:
        na = a + shift(a)
        k = next((kk for cc, kk in todo if cc == b), 0) if camera else 0
        if k:
            sr = it.source_range; rate = sr.start_time.rate; ar = it.media_reference.available_range
            room_ = int((ar.end_time_exclusive() - sr.end_time_exclusive()).rescaled_to(rate).value) if ar else 0
            if room_ < k: raise SystemExit(f'no media to extend at {b} on {track.name}')
            it.source_range = otio.opentime.TimeRange(sr.start_time, sr.duration + otio.opentime.RationalTime(k, rate))
            ext += 1
        new.append([na, na + (b - a) + k, it])
    track.clear(); c = 0
    for a, b, it in sorted(new, key=lambda q: q[0]):
        if a < c: raise SystemExit(f'overlap on {track.name} at {a}')
        if a > c: track.append(otio.schema.Gap(duration=otio.opentime.RationalTime(a - c, FPS)))
        track.append(it); c = b
    if c < NEWEND: track.append(otio.schema.Gap(duration=otio.opentime.RationalTime(NEWEND - c, FPS)))
out.tracks.markers.clear()
for mk in tl.tracks.markers:
    f = fr(mk.marked_range.start_time)
    nm = mk.deepcopy(); nm.marked_range = otio.opentime.TimeRange(otio.opentime.RationalTime(f + shift(f), FPS), mk.marked_range.duration)
    out.tracks.markers.append(nm)
lens = {t.name: fr(t.duration()) for t in out.tracks}
if set(lens.values()) != {NEWEND}: raise SystemExit(f'lengths {lens}')
path_out = os.path.join(S, 'v29.otio'); otio.adapters.write_to_file(out, path_out)
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
        if i == 3: x.SetClipColor('Green'); continue
        if i in (4, 5): x.SetClipColor('Purple'); continue
        f0 = back(int(x.GetStart() - cst))
        hit = next(((c_, e) for sa, sb, c_, e in srcs if f0 is not None and sa <= f0 < sb), None)
        if hit:
            if hit[0]: x.SetClipColor(hit[0])
            x.SetClipEnabled(hit[1])
p.SetCurrentTimeline(created); time.sleep(2)
fades = []
for i, names in TRANS.items():
    for x in created.GetItemListInTrack('video', i):
        if x.GetMediaPoolItem() and x.GetName() in names:
            fades.append(bool(x.AddTransition({'type': 'Cross Dissolve', 'category': 'simple', 'position': 'start',
                                               'alignment': 'right', 'duration': 12})))
for i in (1, 2):
    its = created.GetItemListInTrack('audio', i)
    for a_, b_ in zip(its, its[1:]):
        if b_.GetStart() > a_.GetEnd():
            fa = a_.GetFades() or {}; fb = b_.GetFades() or {}
            a_.SetFades({'FadeIn': fa.get('FadeIn', 0), 'FadeOut': 3}); b_.SetFades({'FadeIn': 3, 'FadeOut': fb.get('FadeOut', 0)})
created.SetVoiceIsolationState(1, {'isEnabled': True, 'amount': 70})
OUTN = OUT + shift(OUT)
V2 = [x for x in created.GetItemListInTrack('video', 2) if x.GetStart() - cst < OUTN]
rs = []
for x in V2:
    a_, b_ = int(x.GetStart() - cst), int(x.GetEnd() - cst); cam = 1 if x.GetClipEnabled() else 2
    if rs and rs[-1][2] == cam and rs[-1][1] == a_: rs[-1][1] = b_
    else: rs.append([a_, b_, cam])
print(json.dumps(dict(name=created.GetName(), frames=int(created.GetEndFrame() - cst), expected=NEWEND, extended_clips=ext,
                      fades=fades, shortest_shot=min(b_ - a_ for a_, b_, _ in rs),
                      clip_markers=sum(len(x.GetMarkers() or {}) for i in (1, 2) for x in created.GetItemListInTrack('video', i))),
                 ensure_ascii=False))

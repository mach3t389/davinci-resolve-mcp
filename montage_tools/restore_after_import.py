"""After an OTIO re-import: restore panel/visual cross-dissolves (same names as in the source), 3-frame audio fades
at every pause, voice isolation on Audio 1. Usage: restore_after_import.py <source> <new>"""
import sys, time, json
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
SRC, NEW = sys.argv[1], sys.argv[2]
_, p = r.project(); ts = {t.GetName(): t for t in r.timelines(p)}
src, new = ts[SRC], ts[NEW]
import re
def base(n): return re.sub(r'_v\d+(\.\w+)$', r'', n)    # Lili01_v001.mov == Lili01_v002.mov (new render of the same panel)
TRANS = {}
for i in range(4, src.GetTrackCount('video') + 1):
    its = src.GetItemListInTrack('video', i)
    starts = {int(x.GetStart()) for x in its if not x.GetMediaPoolItem()}
    seen = {}; TRANS[i] = set()
    for x in its:
        if not x.GetMediaPoolItem(): continue
        k = (base(x.GetName()), seen.get(base(x.GetName()), 0)); seen[base(x.GetName())] = k[1] + 1
        if int(x.GetStart()) in starts: TRANS[i].add(k)          # same name AND same rank (split clips keep theirs)
p.SetCurrentTimeline(new); time.sleep(2)
fades = 0
for i, names in TRANS.items():
    seen = {}
    for x in new.GetItemListInTrack('video', i):
        if not x.GetMediaPoolItem(): continue
        k = (base(x.GetName()), seen.get(base(x.GetName()), 0)); seen[base(x.GetName())] = k[1] + 1
        if k in names:
            fades += bool(x.AddTransition({'type': 'Cross Dissolve', 'category': 'simple', 'position': 'start', 'alignment': 'right', 'duration': 12}))
af = 0
for i in (1, 2):
    its = new.GetItemListInTrack('audio', i)
    for a, b in zip(its, its[1:]):
        if b.GetStart() > a.GetEnd():
            fa = a.GetFades() or {}; fb = b.GetFades() or {}
            a.SetFades({'FadeIn': fa.get('FadeIn', 0), 'FadeOut': 3}); b.SetFades({'FadeIn': 3, 'FadeOut': fb.get('FadeOut', 0)}); af += 1
p.SetCurrentTimeline(src); time.sleep(1); iso_src = src.GetVoiceIsolationState(1)
p.SetCurrentTimeline(new); time.sleep(1)
iso = new.SetVoiceIsolationState(1, {'isEnabled': iso_src['isEnabled'], 'amount': iso_src['amount']})   # readable only on the active timeline
print(json.dumps(dict(transitions_src=sum(len(v) for v in TRANS.values()), transitions_added=fades, audio_fade_pairs=af,
                      isolation=new.GetVoiceIsolationState(1))))

"""Re-place V3.8 markers by source anchoring. Reference positions: V3.3 markers (made on a fresh duplicate of V3.2,
so V3.3 marker frame == V3.2 content frame); content read from V3.2 Audio 1. plan|apply"""
import json, sys
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
MODE = sys.argv[1]
_, p = r.project(); ts = {t.GetName(): t for t in r.timelines(p)}
REF, MK, NEW = ts['Lili V3.2 - coupes sur le silence'], ts['Lili V3.3 - marqueurs coupes et cameras'], ts['Lili V3.8 - citations et pauses replacees']
def tc(f): return f'{int(f) // 1440:02d}:{(int(f) % 1440) / 24:05.2f}'
def items(t):
    st = t.GetStartFrame()
    return [(int(x.GetStart() - st), int(x.GetEnd() - st), x.GetMediaPoolItem().GetClipProperty('File Path'), int(x.GetLeftOffset()))
            for x in t.GetItemListInTrack('audio', 1) if x.GetMediaPoolItem()]
R, N = items(REF), items(NEW)
def anchors(f):
    out = []
    for a, b, fi, s0 in R:
        if b == f: out.append(('out', fi, s0 + b - a))
        if a <= f < b: out.append(('in', fi, s0 + f - a, a == f))
    return out
def locate(anc, near):
    cands = []
    for an in anc:
        for a, b, fi, s0 in N:
            if fi != an[1]: continue
            s1 = s0 + b - a
            if an[0] == 'in' and s0 <= an[2] < s1:
                pos = a + an[2] - s0; at_join = pos == a
                cands.append((0 if (at_join or not an[3]) else 2, abs(pos - near), pos, 'debut' if at_join else 'dans'))
            if an[0] == 'out' and s0 < an[2] <= s1:
                pos = a + an[2] - s0; cands.append((0 if pos == b else 1, abs(pos - near), pos, 'fin' if pos == b else 'dans'))
    cands = [c for c in cands if c[1] < 24 * 120]
    return min(cands) if cands else None
cur = NEW.GetMarkers()
SPECIAL = {'Fin de l\'exemple déplacé; pause du panneau Exercice thèmes': (11489, 'Pause du panneau Exercice thèmes, déplacée en V3.8 avant « Quantifier » (fin de phrase). Rien de retiré.'),
           'Pause insérée (exercice profondeur), rien de retiré.': (21350, 'Pause insérée (exercice profondeur), déplacée en V3.8 après « trop faible. » (fin de phrase). Rien de retiré.')}
import os
FIX = {}
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'v33.py'), encoding='utf8').read().split('FIX = ', 1)[1].split('_, p = r.project()')[0].join(['FIX = ', '']))
cuts = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'my_cuts.json'), encoding='utf8'))
note_at = {}
for e in cuts:
    fx = FIX.get(e['tc'], 'auto')
    if fx is None: continue
    if fx == 'auto':
        if e['removed_from_edit_s'] and e.get('mine'):
            nt = f"Retiré ({e['removed_from_edit_s']} s) : « {e['mine']} »"
            if e['pause_s'] > 1: nt += f" — remplacé par un panneau ({e['pause_s']} s)."
        else: nt = 'Pause insérée, rien de retiré.'
    else: nt = fx[0]
    note_at[nt[:900]] = int(e['at'])
refs = [(int(float(k)), v) for k, v in REF.GetMarkers().items()]
for k, v in NEW.GetMarkers().items():
    if v['name'] != 'Coupe Claude': continue
    if v['note'] not in note_at: print('note sans coupe:', v['note'][:60]); continue
    refs.append((note_at[v['note']], v))
V37 = ts['Lili V3.7 - bouts courts sur l autre camera']
for k, v in V37.GetMarkers().items():
    if any(v['note'].startswith(x) for x in SPECIAL): refs.append((note_at.get(v['note'], int(float(k))), v))
plan = []
for f, v in sorted(refs, key=lambda x: x[0]):
    note = v['note']
    sp = next((s for k, s in SPECIAL.items() if note.startswith(k)), None)
    near = f
    if note.startswith("Retiré (6.67 s) : « c'est de mélanger"):
        loc = locate([('out', next(fi for a_, b_, fi, s0 in R if s0 == 41056), 41336)], f)
        plan.append((f, loc[2], v, note, 'fin cousine')); continue
    if sp: plan.append((f, sp[0], v, sp[1], 'deplace')); continue
    if f == 0: plan.append((f, 0, v, note, 'debut')); continue
    loc = locate(anchors(f), near)
    if not loc: plan.append((f, None, v, note, 'introuvable')); continue
    plan.append((f, loc[2], v, note, loc[3]))
# notes updated for restored content
for i, (f, nf, v, note, how) in enumerate(plan):
    if note.startswith('Retiré (6.67 s) : « c\'est de mélanger'):
        note += ' V3.8 : « Mais si votre objectif, c\'est vraiment » rétabli (phrase complète).'
    if 'Numéro un, en réécrivant' in note:
        note = ('Retiré : « Numéro un, en réécrivant et en réécrivant, mais surtout en réécrivant. » (titre lu, panneau Réécrire). '
                'V3.8 : la citation d\'Oates est de nouveau lue par Lili sous son panneau.')
    plan[i] = (f, nf, v, note, how)
moved = 0
for f, nf, v, note, how in plan:
    near = next((int(float(k)) for k, w in cur.items() if w['name'] == v['name'] and w['note'][:40] == v['note'][:40]), None)
    flag = '' if near == nf else f'  (etait {tc(near) if near is not None else "absent"})'
    if near != nf: moved += 1
    print(f'{tc(f)} -> {tc(nf) if nf is not None else "??"} [{how}] {v["color"]} {v["name"][:14]} | {note[:60]}{flag}')
print('a deplacer', moved, 'sur', len(plan))
if MODE == 'apply':
    for k in list(cur): NEW.DeleteMarkerAtFrame(int(float(k)))
    ok = 0
    for f, nf, v, note, how in plan:
        if nf is None: continue
        ok += bool(NEW.AddMarker(nf, v['color'], v['name'], note[:900], max(1, int(v['duration'])), v.get('customData', '')))
    print('marqueurs poses', ok, '/', len(plan), 'total', len(NEW.GetMarkers()))

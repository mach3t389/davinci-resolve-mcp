"""Non-destructive review markers on native timeline duplicates."""
import uuid

def create_review_copy(project, find, summary, edits, timeline_id, markers, name):
    _, p = project()
    source = find(p, timeline_id)
    before = edits.digest(source)
    length = source.GetEndFrame() - source.GetStartFrame()
    occupied = set(source.GetMarkers() or {})
    for m in markers:
        f, d = m['frame'], m['duration']
        if type(f) is not int or type(d) is not int or f < 0 or d < 1 or f+d > length:
            raise ValueError('Invalid marker boundaries; no copy created.')
        if m['color'] not in ('Green','Red','Yellow') or f in occupied:
            raise ValueError('Invalid color or occupied marker position; no copy created.')
        occupied.add(f)
    previous = p.GetCurrentTimeline()
    copy = source.DuplicateTimeline(name + '_' + uuid.uuid4().hex[:6])
    if not copy or copy.GetUniqueId() == source.GetUniqueId():
        raise RuntimeError('Fresh duplication failed.')
    result = {'timeline':summary(copy), 'markers_added':0, 'complete':False}
    def contents(t):
        tracks = edits.snapshot(t)['tracks']
        for track in tracks:
            for item in track['items']:
                item.pop('id',None)
        return tracks
    try:
        if contents(source) != contents(copy):
            raise RuntimeError('Duplicate clip structure differs from source.')
        for m in markers:
            tag = 'client-review:' + uuid.uuid4().hex
            if not copy.AddMarker(m['frame'],m['color'],m['name'],m['note'],m['duration'],tag):
                raise RuntimeError('Marker insertion failed; inspect partial copy, do not retry.')
            actual = (copy.GetMarkers() or {}).get(m['frame'],{})
            if any(actual.get(k)!=v for k,v in {'color':m['color'],'name':m['name'],'note':m['note'],'duration':m['duration'],'customData':tag}.items()):
                raise RuntimeError('Marker read-back failed; inspect partial copy.')
            result['markers_added'] += 1
        result['source_unchanged'] = edits.digest(source)==before
        result['clip_structure_preserved'] = contents(source)==contents(copy)
        result['complete'] = result['source_unchanged'] and result['clip_structure_preserved']
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        result['selection_restored'] = bool(p.SetCurrentTimeline(previous)) if previous else None
    return result

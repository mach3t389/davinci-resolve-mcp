"""Read-only picture checks of the existing camera revision."""
import json
from camera_bridge import WORK,require

def export_review(project,find):
    plan=json.loads((WORK/'camera-edit-plan.json').read_text(encoding='utf8'))
    result=json.loads((WORK/'camera-edit-result.json').read_text(encoding='utf8'))
    require(result.get('complete'),'Finish structural verification before exporting review frames.')
    _,p=project();previous=p.GetCurrentTimeline()
    source=find(p,plan['timeline_id']);copy=find(p,result['timeline']['id'])
    outputs=[]
    try:
        p.SetCurrentTimeline(copy)
        overlays=[(c.GetStart(),c.GetEnd()) for i in range(4,copy.GetTrackCount('video')+1)
                  for c in copy.GetItemListInTrack('video',i) or []]
        for cam in (1,3,2):
            shot=next(s for s in plan['shots'] if s['camera']==cam and s['start']>copy.GetStartFrame()+300*24
                and not any(a<=(s['start']+s['end'])//2<z for a,z in overlays))
            frame=(shot['start']+shot['end'])//2
            output=WORK/f'camera-review-{cam}.png';reference=WORK/f'camera-reference-{cam}.png'
            opts=dict(outputType='file',maxDimension=1280)
            p.SetCurrentTimeline(source)
            c=next(c for c in source.GetItemListInTrack('video',cam) if c.GetStart()<=frame<c.GetEnd())
            ref=c.ExportFrame(int(frame-c.GetStart()),dict(opts,filePath=str(reference)))
            p.SetCurrentTimeline(copy)
            exported=copy.ExportFrame(int(frame-copy.GetStartFrame()),dict(opts,filePath=str(output)))
            item=dict(camera=cam,elapsed_seconds=(frame-copy.GetStartFrame())/24,
                output=str(output),reference=str(reference),output_exported=exported,reference_exported=ref)
            if output.exists() and reference.exists():
                import hashlib
                item['identical_export_bytes']=hashlib.sha256(output.read_bytes()).digest()==hashlib.sha256(reference.read_bytes()).digest()
            outputs.append(item)
    finally:p.SetCurrentTimeline(previous)
    (WORK/'camera-review-frames.json').write_text(json.dumps(outputs,indent=2),encoding='utf8')
    return dict(frames=outputs)

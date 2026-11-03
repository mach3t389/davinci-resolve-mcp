"""Native camera editing on fresh duplicates only; never delete client clips."""
import json
import uuid
from pathlib import Path

WORK = Path(__file__).parent / 'work'

def clip_state(c):
    m = c.GetMediaPoolItem()
    return dict(name=c.GetName(), start=c.GetStart(True), end=c.GetEnd(True),
                source_start=c.GetSourceStartFrame(), source_end=c.GetSourceEndFrame(),
                left=c.GetLeftOffset(), right=c.GetRightOffset(), enabled=c.GetClipEnabled(),
                source_start_time=c.GetSourceStartTime(),source_end_time=c.GetSourceEndTime(),
                media=m.GetUniqueId() if m else None, properties=c.GetProperty(), speed=c.GetSpeed())

def snapshot(t):
    return dict(start=t.GetStartFrame(), end=t.GetEndFrame(), inout=t.GetMarkInOut(),
                markers=t.GetMarkers(), tracks=[dict(kind=k,index=i,name=t.GetTrackName(k,i),
                enabled=t.GetIsTrackEnabled(k,i), items=[clip_state(c) for c in t.GetItemListInTrack(k,i) or []])
                for k in ('video','audio','subtitle') for i in range(1,t.GetTrackCount(k)+1)])

def require(value, message):
    if not value:
        raise RuntimeError(message)

def add_piece(p, t, original, a, z, track, scratch):
    """Clone attributes, restore native timeline-unit offsets, then move into empty space."""
    state=clip_state(original)
    added=p.GetMediaPool().AppendToTimeline([dict(mediaPoolItem=original.GetMediaPoolItem(),
        startFrame=0, endFrame=int(original.GetMediaPoolItem().GetClipProperty('Frames'))-1, mediaType=1,
        trackIndex=scratch, recordFrame=t.GetStartFrame())])
    require(added and len(added)==1,'Append failed; inspect partial copy.')
    c=added[0]
    require(original.CopyAttributes([c],dict(allVideoAttributes=True,allClipAttributes=True)),'Attribute copy failed.')
    require(c.SetRightOffset(state['right']+state['end']-z,False,False),'Right trim failed.')
    require(c.SetLeftOffset(state['left']+a-state['start'],False,False),'Left trim failed.')
    require(c.SetPosition(dict(trackIndex=track,startFrame=a),False),'Move failed.')
    actual=clip_state(c)
    require(actual['start']==a and actual['end']==z,'Native segment timing mismatch.')
    require(actual['properties']==state['properties'],'Native segment properties mismatch.')
    expected_time=state['source_start_time']+(a-state['start'])/float(t.GetSetting('timelineFrameRate'))
    require(abs(actual['source_start_time']-expected_time)<0.0001,'Source phase differs; stop before batch.')
    return c

def normalized_coverage(clips,start,end):
    intervals=[]
    for c in clips:
        a,z=max(start,c.GetStart()),min(end,c.GetEnd())
        if a>=z:continue
        if intervals and intervals[-1][1]==a:intervals[-1][1]=z
        else:intervals.append([a,z])
    return intervals

class CameraEdits:
    def __init__(self,project,find,summary):
        self.project,self.find,self.summary=project,find,summary
        self.plans={}

    def preview(self,timeline_id,start_seconds=128,rhythm='long'):
        from camera_plan import make_plan
        _,p=self.project();source=self.find(p,timeline_id);previous=p.GetCurrentTimeline()
        try:
            require(p.SetCurrentTimeline(source),'Source selection failed.')
            before=snapshot(source);fps=float(source.GetSetting('timelineFrameRate'))
            require(fps==24,'This workflow currently requires a 24 fps timeline.')
            out=before['inout'].get('video',{}).get('out')
            require(out is not None,'Set a timeline video Out before planning.')
            start=source.GetStartFrame()+round(start_seconds*fps)
            end=source.GetStartFrame()+out+1
            require(0<=start_seconds and start<end<=source.GetEndFrame(),'Invalid camera range.')
            reference=source.GetItemListInTrack('video',1) or []
            coverage=normalized_coverage(reference,start,end)
            for track in range(1,4):
                items=source.GetItemListInTrack('video',track) or []
                require(normalized_coverage(items,start,end)==coverage,'Camera coverage differs; manual review needed.')
                for c in items:
                    if c.GetEnd()<=start or c.GetStart()>=end:continue
                    require(c.GetType()=='video' and c.GetMediaPoolItem(),'Only file camera video is supported.')
                    require(c.GetSpeed().get('Percentage')==100,'Variable speed is unsupported.')
                    require(c.GetFusionCompCount()==0,'Camera Fusion compositions require manual review.')
            clips=[dict(start=c.GetStart(),end=c.GetEnd(),name=c.GetName(),
                source_start_frame=c.GetSourceStartFrame(),source_end_frame=c.GetSourceEndFrame()) for c in reference]
            plan=make_plan(clips,start,end,int(fps),rhythm)
            pid=uuid.uuid4().hex
            plan.update(plan_id=pid,timeline_id=timeline_id,source_snapshot=before,attempted=False)
            self.plans[pid]=plan
            WORK.mkdir(exist_ok=True)
            (WORK/'camera-edit-plan.json').write_text(json.dumps(plan,indent=2),encoding='utf8')
            return {k:v for k,v in plan.items() if k!='source_snapshot'}
        finally:p.SetCurrentTimeline(previous)

    def create(self,plan_id,copy_name):
        plan=self.plans.get(plan_id)
        require(plan and not plan['attempted'],'Unknown or already attempted plan; inspect the saved result.')
        _,p=self.project();source=self.find(p,plan['timeline_id']);previous=p.GetCurrentTimeline()
        require(p.SetCurrentTimeline(source),'Source selection failed.')
        require(snapshot(source)==plan['source_snapshot'],'Source changed since preview.')
        plan['attempted']=True
        result=dict(plan_id=plan_id,complete=False,processed_clips=0,new_segments=0)
        path=WORK/'camera-edit-result.json'
        def save():path.write_text(json.dumps(result,indent=2),encoding='utf8')
        save()
        try:
            t=source.DuplicateTimeline(copy_name)
            require(t and t.GetUniqueId()!=source.GetUniqueId(),'Fresh duplication failed.')
            result['timeline']=self.summary(t);save()
            require(p.SetCurrentTimeline(t),'Copy selection failed.')
            before=plan['source_snapshot'];copied=snapshot(t)
            require(copied['tracks']==before['tracks'] and copied['markers']==before['markers'],'Duplicate structure differs.')
            out=before['inout']['video']['out']
            require(t.SetMarkInOut(before['inout']['video'].get('in',0),out),'Out restoration failed.')
            require(t.AddTrack('video'),'Scratch track creation failed.')
            scratch=t.GetTrackCount('video')
            shots=plan['shots']
            boundaries=sorted({x[k] for x in shots for k in ('start','end')})
            for track in range(1,4):
                originals=source.GetItemListInTrack('video',track) or []
                copies=list(t.GetItemListInTrack('video',track) or [])
                require(len(originals)==len(copies),'Camera clip count differs.')
                for original,c in zip(originals,copies):
                    state=clip_state(c);a,z=int(state['start']),int(state['end'])
                    if z<=plan['start'] or a>=plan['end']:continue
                    cuts=[a]+[b for b in boundaries if a<b<z]+[z]
                    if len(cuts)>2:
                        require(c.SetRightOffset(state['right']+z-cuts[1],False,False),'Original video trim failed.')
                    pieces=[c]
                    for left,right in zip(cuts[1:-1],cuts[2:]):
                        pieces.append(add_piece(p,t,original,left,right,track,scratch))
                        result['new_segments']+=1
                    for piece,left,right in zip(pieces,cuts,cuts[1:]):
                        enabled=state['enabled']
                        if plan['start']<=left and right<=plan['end']:
                            matching=[s for s in shots if s['start']<=left and right<=s['end']]
                            require(len(matching)==1,'Segment not covered by exactly one shot.')
                            enabled=matching[0]['camera']==track
                        require(piece.SetClipEnabled(enabled),'Video enabled-state update failed.')
                        require(piece.GetClipEnabled()==enabled,'Enabled-state read-back failed.')
                        actual=clip_state(piece)
                        expected_source_time=state['source_start_time']+(left-a)/plan['fps']
                        require(actual['start']==left and actual['end']==right,'Segment boundary read-back failed.')
                        require(abs(actual['source_start_time']-expected_source_time)<0.0001,'Source phase read-back failed.')
                        require(actual['properties']==state['properties'],'Clip property preservation failed.')
                    result['processed_clips']+=1
                    save()
            require(not t.GetItemListInTrack('video',scratch),'Scratch track is not empty.')
            require(t.DeleteTrack('video',scratch),'Empty scratch track removal failed.')
            after=snapshot(t)
            for oldtr,newtr in zip(before['tracks'],after['tracks']):
                if oldtr['kind']!='video' or oldtr['index']>3:
                    require(oldtr==newtr,'An unrelated track changed.')
            for track in range(1,4):
                require(normalized_coverage(source.GetItemListInTrack('video',track),plan['start'],plan['end'])==
                    normalized_coverage(t.GetItemListInTrack('video',track),plan['start'],plan['end']),
                    'Camera coverage was not fully preserved.')
            for s in shots:
                for track in range(1,4):
                    intersect=[c for c in t.GetItemListInTrack('video',track) if c.GetStart()<s['end'] and c.GetEnd()>s['start']]
                    require(intersect and all(c.GetClipEnabled()==(track==s['camera']) for c in intersect),'Shot visibility differs.')
            require(after['start']==before['start'] and after['end']==before['end'],'Timeline duration changed.')
            require(after['markers']==before['markers'],'Original markers changed.')
            # Native clip insertion clears range marks; restore them after editing.
            require(t.SetMarkInOut(before['inout']['video'].get('in',0),out),'Final Out restoration failed.')
            require(t.GetMarkInOut()['video']['out']==out,'Output mark differs.')
            p.SetCurrentTimeline(source)
            require(snapshot(source)==before,'Original source snapshot changed.')
            result.update(complete=True,source_unchanged=True,audio_and_upper_tracks_preserved=True,
                all_camera_footage_preserved=True,source_phase_verified=True,percentages=plan['percentages'],
                shot_count=len(shots),short_shots=plan['short_shots'])
        except Exception as e:result['error']=str(e)
        finally:
            result['selection_restored']=bool(p.SetCurrentTimeline(previous));save()
        return result

def probe(project,find,summary,timeline_id):
    _,p=project(); source=find(p,timeline_id)
    previous=p.GetCurrentTimeline(); p.SetCurrentTimeline(source); before=snapshot(source)
    prior=WORK/'camera-native-probe.json'
    record=json.loads(prior.read_text(encoding='utf8')) if prior.exists() else {}
    if record.get('error')=='Native duplicate differs.':
        t=find(p,record['timeline']['id'])
    else:
        t=source.DuplicateTimeline('MCP_CAMERA_VALIDATION_'+uuid.uuid4().hex[:6])
    require(t and t.GetUniqueId()!=source.GetUniqueId(),'Duplication failed.')
    result=dict(timeline=summary(t),complete=False)
    WORK.mkdir(exist_ok=True)
    path=WORK/'camera-native-probe.json'
    path.write_text(json.dumps(result,indent=2),encoding='utf8')
    try:
        require(p.SetCurrentTimeline(t),'Selection failed.')
        copied=snapshot(t)
        require(copied['tracks']==before['tracks'] and copied['markers']==before['markers'],'Native duplicate differs.')
        # Native duplication omits timeline In/Out marks; restore Out explicitly.
        out=before['inout']['video']['out']
        require(t.SetMarkInOut(0,out),'Restore Out failed.')
        track=3
        c=next(c for c in t.GetItemListInTrack('video',track) if c.GetStart()>=source.GetStartFrame()+128*24)
        original=next(x for x in source.GetItemListInTrack('video',track) if x.GetStart()==c.GetStart())
        s=clip_state(c); split=int(s['start']+(s['end']-s['start'])//2)
        require(t.AddTrack('video'),'Scratch track failed.')
        scratch=t.GetTrackCount('video')
        require(c.SetRightOffset(s['right']+s['end']-split,False,False),'First half trim failed.')
        new=add_piece(p,t,original,split,int(s['end']),track,scratch)
        require(not t.GetItemListInTrack('video',scratch),'Scratch not empty.')
        require(t.DeleteTrack('video',scratch),'Empty scratch cleanup failed.')
        result.update(first=clip_state(c),second=clip_state(new),original=s)
        after=snapshot(t)
        for oldtr,newtr in zip(before['tracks'],after['tracks']):
            if oldtr['kind']!='video' or oldtr['index']!=track:
                require(oldtr==newtr,'Unrelated track changed.')
        p.SetCurrentTimeline(source)
        require(snapshot(source)==before,'Source changed.')
        result.update(complete=True,source_unchanged=True,unrelated_tracks_preserved=True)
    except Exception as e:
        result['error']=str(e)
    finally:
        result['selection_restored']=bool(p.SetCurrentTimeline(previous))
        path.write_text(json.dumps(result,indent=2),encoding='utf8')
    return result

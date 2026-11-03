"""Reinspect an existing attempt; never repeat editing after an error."""
import json
from camera_bridge import WORK,snapshot,clip_state,require,normalized_coverage

def verify_existing(project,find,summary,open_copy=True):
    plan=json.loads((WORK/'camera-edit-plan.json').read_text(encoding='utf8'))
    path=WORK/'camera-edit-result.json'
    result=json.loads(path.read_text(encoding='utf8'))
    require(result['plan_id']==plan['plan_id'],'Plan/result identity differs.')
    require('timeline' in result,'No known copy to verify.')
    _,p=project();source=find(p,plan['timeline_id']);copy=find(p,result['timeline']['id'])
    require(source.GetUniqueId()!=copy.GetUniqueId(),'Copy identity check failed.')
    previous=p.GetCurrentTimeline()
    try:
        p.SetCurrentTimeline(source)
        before=snapshot(source)
        require(before==plan['source_snapshot'],'Original source differs from preview.')
        source_items={i:list(source.GetItemListInTrack('video',i) or []) for i in range(1,4)}
        original_states={i:[clip_state(c) for c in source_items[i]] for i in range(1,4)}
        p.SetCurrentTimeline(copy)
        after=snapshot(copy)
        require(len(after['tracks'])==len(before['tracks']),'Track count differs.')
        require(after['start']==before['start'] and after['end']==before['end'],'Timeline bounds differ.')
        require(after['markers']==before['markers'],'Source markers differ.')
        for a,z in zip(before['tracks'],after['tracks']):
            if a['kind']!='video' or a['index']>3:
                require(a==z,'Audio, subtitles or upper video differs.')
        pieces_count=0; max_phase_error=0.0
        for track in range(1,4):
            pieces=copy.GetItemListInTrack('video',track) or []
            require(normalized_coverage(pieces,copy.GetStartFrame(),copy.GetEndFrame())==
                    normalized_coverage(source_items[track],source.GetStartFrame(),source.GetEndFrame()),'Camera coverage differs.')
            for c in pieces:
                cs=clip_state(c)
                matches=[s for s in original_states[track] if s['start']<=cs['start'] and cs['end']<=s['end']]
                require(len(matches)==1,'Segment crosses original boundary or is not mapped.')
                s=matches[0]
                require(cs['media']==s['media'] and cs['properties']==s['properties'],'Source/attributes differ.')
                expected=s['source_start_time']+(cs['start']-s['start'])/plan['fps']
                error=abs(expected-cs['source_start_time']);max_phase_error=max(max_phase_error,error)
                require(error<0.0001,'Source phase mismatch.')
                if cs['end']<=plan['start'] or cs['start']>=plan['end']:
                    require(cs==s,'Outside-range camera clip changed.')
                else:
                    shots=[x for x in plan['shots'] if x['start']<=cs['start'] and cs['end']<=x['end']]
                    require(len(shots)==1,'Unscheduled segment.')
                    require(cs['enabled']==(shots[0]['camera']==track),'Camera enabled state differs.')
                pieces_count+=1
        # Use the exact elapsed values returned from the source's marks.
        out=before['inout']['video']['out'];start=copy.GetStartFrame()
        require(copy.SetMarkInOut(before['inout']['video'].get('in',0),out),'Set Out failed.')
        marks=copy.GetMarkInOut()
        require(marks.get('video',{}).get('out')==out and marks.get('audio',{}).get('out')==out,'Out read-back differs.')
        result.update(complete=True,source_unchanged=True,audio_and_upper_tracks_preserved=True,
            all_camera_footage_preserved=True,source_phase_verified=True,max_source_time_error_seconds=max_phase_error,
            percentages=plan['percentages'],shot_count=len(plan['shots']),short_shots=plan['short_shots'],
            output_marks=marks,verified_camera_segments=pieces_count)
        result.pop('verification_error',None)
        if 'error' in result:result['resolved_error']=result.pop('error')
        p.SetCurrentTimeline(source)
        require(snapshot(source)==before,'Source changed during verification.')
        if open_copy:
            require(p.SetCurrentTimeline(copy),'Open copy failed.')
            frame=plan['shots'][0]['start']+12
            fps=plan['fps'];seconds,ff=divmod(frame,fps);hours,seconds=divmod(seconds,3600);minutes,seconds=divmod(seconds,60)
            copy.SetCurrentTimecode(f'{hours:02d}:{minutes:02d}:{seconds:02d}:{ff:02d}')
            result['copy_opened']=True
        else:p.SetCurrentTimeline(previous)
        result['project_saved']=bool(p.GetMediaPool()) and bool(project()[0].GetProjectManager().SaveProject())
    except Exception as e:
        result.update(complete=False,verification_error=str(e))
        p.SetCurrentTimeline(previous)
    path.write_text(json.dumps(result,indent=2),encoding='utf8')
    return result

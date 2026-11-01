"""Piecewise alignment against edited camera-one sections, preserving its timeline."""
import uuid


def mapping_source_frame(anchor_source, anchor_record, record, rate_ratio=1.0):
    return round(anchor_source + (record - anchor_record) * rate_ratio)


class SectionSync:
    def __init__(self, sync):
        self.sync = sync
        self.plans = {}

    def preview(self, timeline_id, target_media_id, reference_video_track=1, reference_audio_track=1):
        _, project = self.sync.project()
        source = self.sync.find(project, timeline_id)
        media = self.sync.media(project).get(target_media_id)
        if not media:
            raise ValueError('Target media not found.')
        target_fps = float(media[0].GetClipProperty('FPS'))
        fps = float(source.GetSetting('timelineFrameRate'))
        if abs(target_fps-fps) > .001:
            raise ValueError('Section sync currently requires target FPS matching timeline FPS.')
        videos = source.GetItemListInTrack('video', reference_video_track) or []
        audios = source.GetItemListInTrack('audio', reference_audio_track) or []
        if not videos or not audios:
            raise ValueError('Reference video and audio tracks must be populated.')
        groups = []
        for video in videos:
            media_id = video.GetMediaPoolItem().GetUniqueId()
            if media_id == target_media_id:
                raise ValueError('Choose an original reference track, not a prior sync output.')
            rate = float(video.GetMediaPoolItem().GetClipProperty('FPS'))
            if abs(rate-fps) > .001:
                raise ValueError('Reference FPS must match timeline FPS for this first implementation.')
            affine = float(video.GetLeftOffset(True)) - video.GetStart(True)
            group = next((g for g in groups if g['media_id']==media_id and abs(g['affine']-affine)<=2),None)
            if group is None:
                candidates = [a for a in audios if a.GetMediaPoolItem() and
                    a.GetMediaPoolItem().GetUniqueId()==media_id and
                    abs(float(a.GetLeftOffset(True))-a.GetStart(True)-affine)<=2]
                if not candidates:
                    raise ValueError('A reference section has no matching continuous audio. No guessed mapping.')
                anchor = max(candidates,key=lambda a:a.GetDuration())
                group = {'media_id':media_id,'affine':affine,'reference_audio_id':anchor.GetUniqueId(),
                         'reference_name':anchor.GetName(),'reference_record':anchor.GetStart(True),
                         'reference_source':float(anchor.GetLeftOffset(True)),'segments':[]}
                groups.append(group)
            group['segments'].append({'start':video.GetStart(True),'end':video.GetEnd(True),
                                      'source_start':float(video.GetLeftOffset(True)), 'name':video.GetName()})
        token=uuid.uuid4().hex
        self.plans[token]={'state':'previewed','project_id':project.GetUniqueId(), 'source_id':timeline_id,
                          'target_id':target_media_id,'digest':self.sync.edits.digest(source),'groups':groups}
        return {'plan_id':token,'groups':groups,'source':self.sync.summary(source),
                'operation':'Analyze each continuous reference section, then append bounded target portions on a fresh source copy.',
                'requires_acoustic_review':True}

    def create(self, plan_id):
        plan=self.plans.get(plan_id)
        if not plan or plan['state']!='previewed':
            raise ValueError('Unknown/already attempted section plan; inspect partial copies instead of retrying.')
        _,p=self.sync.project()
        if p.GetUniqueId()!=plan['project_id']:
            raise ValueError('Project changed.')
        source=self.sync.find(p,plan['source_id'])
        if self.sync.edits.digest(source)!=plan['digest']:
            raise ValueError('Source changed.')
        previous=p.GetCurrentTimeline()
        plan['state']='attempted'
        mappings=[]
        plan['analyses']=[]
        next_search_start=0
        try:
            for group in plan['groups']:
                pair=self.sync.preview(plan['source_id'],group['reference_audio_id'],plan['target_id'],'waveform',next_search_start)
                result=self.sync.create(pair['plan_id'])
                plan['analyses'].append(result)
                if not result.get('technical_checks_passed'):
                    raise RuntimeError('A section failed structural sync checks; no final timeline created. Analysis copies preserved.')
                copy=self.sync.find(p,result['timeline']['id'])
                audio=copy.GetItemListInTrack('audio',copy.GetTrackCount('audio'))[0]
                mappings.append((group,float(audio.GetLeftOffset(True)),audio.GetStart(True)))
                target_at_anchor=mapping_source_frame(float(audio.GetLeftOffset(True)),audio.GetStart(True),group['reference_record'])
                mapped_ends=[round(target_at_anchor+s['source_start']-group['reference_source']+s['end']-s['start'])
                             for s in group['segments']]
                # Search recordings in source order, keeping a 10-second overlap guard.
                next_search_start=max(0,max(mapped_ends)-round(float(source.GetSetting('timelineFrameRate'))*10))
            media=self.sync.media(p)[plan['target_id']][0]
            total=int(media.GetClipProperty('Frames'))
            segments=[]
            for group, target_source, target_record in mappings:
                target_at_anchor=mapping_source_frame(target_source,target_record,group['reference_record'])
                for segment in group['segments']:
                    # Source-coordinate delta preserves manual edits in the camera-one track.
                    start=round(target_at_anchor+segment['source_start']-group['reference_source'])
                    duration=round(segment['end']-segment['start'])
                    if start<0 or start+duration>total:
                        raise RuntimeError('Target does not cover a requested section; no arbitrary trim or placement.')
                    segments.append(dict(segment,target_start=start,target_end_exclusive=start+duration))
            if self.sync.edits.digest(source)!=plan['digest']:
                raise RuntimeError('Source changed during analysis; no final timeline created.')
            final=source.DuplicateTimeline('MCP_SYNC_SECTIONS_'+uuid.uuid4().hex[:8])
            if not final or final.GetUniqueId()==source.GetUniqueId():
                raise RuntimeError('Could not create fresh final copy.')
            plan['copy_id']=final.GetUniqueId()
            baseline=self.sync.edits.snapshot(final)
            if not p.SetCurrentTimeline(final) or not final.AddTrack('video') or not final.AddTrack('audio','stereo'):
                raise RuntimeError('Track setup failed; partial copy preserved.')
            vi,ai=final.GetTrackCount('video'),final.GetTrackCount('audio')
            checked=[]
            for seg in segments:
                pair=[]
                for kind,index in ((1,vi),(2,ai)):
                    added=p.GetMediaPool().AppendToTimeline([{'mediaPoolItem':media,'mediaType':kind,'trackIndex':index,
                        'startFrame':seg['target_start'],'endFrame':seg['target_end_exclusive'],'recordFrame':seg['start']}])
                    if not added or len(added)!=1:
                        raise RuntimeError('Append failed; partial result preserved, no automatic retry.')
                    pair.extend(added)
                if not final.SetClipsLinked(pair,True):
                    raise RuntimeError('Pair linking failed.')
                positions=[(c.GetStart(True),c.GetEnd(True)) for c in pair]
                inpoints=[float(c.GetLeftOffset(True)) for c in pair]
                if any(abs(a-seg['start'])>.001 or abs(b-seg['end'])>.001 for a,b in positions) or any(abs(v-seg['target_start'])>.001 for v in inpoints):
                    raise RuntimeError('Imported boundaries differ from plan; partial copy preserved for inspection.')
                checked.append(seg)
            current={(t['kind'],t['index']):t['items'] for t in self.sync.edits.snapshot(final)['tracks']}
            unchanged=all(current.get((t['kind'],t['index']))==t['items'] for t in baseline['tracks'])
            result={'timeline':self.sync.summary(final),'segments':checked,
                    'original_unchanged':self.sync.edits.digest(source)==plan['digest'],
                    'existing_copy_items_unchanged':unchanged,'technical_checks_passed':unchanged,
                    'alignment_verified':False,'requires_acoustic_review':True,
                    'analysis_copy_ids':[a['timeline']['id'] for a in plan['analyses']]}
            plan['state']='completed';plan['result']=result
            return result
        except Exception as exc:
            plan['error']=str(exc)
            raise
        finally:
            restored=bool(p.SetCurrentTimeline(previous)) if previous else None
            if plan.get('result'):
                plan['result']['original_selection_restored']=restored

    def status(self, plan_id):
        p=self.plans.get(plan_id)
        if not p:raise ValueError('Unknown session plan.')
        return {k:p.get(k) for k in ('state','copy_id','analyses','result','error')}

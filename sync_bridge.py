"""Native Resolve alignment on fresh copies only; no source mutations or deletes."""
import uuid
from datetime import datetime
from pathlib import Path


def assess_pair(before, after, source_before, source_after):
    deltas = [a[0] - b[0] for a, b in zip(after, before)]
    trims = [{'left_frames': a[0] - b[0], 'right_frames': b[1] - a[1]}
             for a, b in zip(source_after, source_before)]
    paired = (deltas[0] == deltas[1] and after[0] == after[1]
              and source_after[0] == source_after[1])
    retained = (before == after or all(a[1]-a[0] == b[1]-b[0] for a,b in zip(after,before)))
    return {'offset_frames': deltas, 'paired_movement_matches': paired,
            'target_duration_preserved': retained, 'source_boundary_changes': trims,
            'source_ranges_match': source_after[0] == source_after[1]}


class SyncBridge:
    def __init__(self, project, find, summary, edits):
        self.project, self.find, self.summary, self.edits = project, find, summary, edits
        self.plans = {}

    def media(self, project):
        result = {}
        def walk(folder, path):
            path = path + [folder.GetName()]
            for clip in folder.GetClipList() or []:
                result[clip.GetUniqueId()] = (clip, '/'.join(path))
            for child in folder.GetSubFolderList() or []:
                walk(child, path)
        walk(project.GetMediaPool().GetRootFolder(), [])
        return result

    def inventory(self):
        resolve, project = self.project()
        clips = []
        for key, (clip, folder) in self.media(project).items():
            props = clip.GetClipProperty() or {}
            if props.get('Type') == 'Timeline':
                continue
            clips.append({'media_id': key, 'name': clip.GetName(), 'bin': folder,
                          'fps': props.get('FPS'), 'frames': props.get('Frames'),
                          'audio_channels': props.get('Audio Ch'), 'file': props.get('File Path'),
                          'online': props.get('Online Status')})
        return {'project_id': project.GetUniqueId(), 'clips': clips,
                'native_alignment_constant': getattr(resolve, 'AUTO_ALIGN_CLIPS_USING_WAVEFORM', None)}

    def reference(self, timeline, reference_id):
        for index in range(1, timeline.GetTrackCount('audio') + 1):
            for ordinal, clip in enumerate(timeline.GetItemListInTrack('audio', index) or []):
                if clip.GetUniqueId() == reference_id:
                    return clip, index, ordinal
        raise ValueError('Select an existing AUDIO timeline item as the reference.')

    def preview(self, timeline_id, reference_audio_id, target_media_id, mode, search_start_frame=0):
        if mode not in ('waveform', 'timecode'):
            raise ValueError('Mode must be waveform or timecode; no automatic fallback.')
        resolve, project = self.project()
        timeline = self.find(project, timeline_id)
        reference, track, ordinal = self.reference(timeline, reference_audio_id)
        entry = self.media(project).get(target_media_id)
        if not entry:
            raise ValueError('Target media is not in the current project.')
        media, folder = entry
        props = media.GetClipProperty() or {}
        if type(search_start_frame) is not int or not 0 <= search_start_frame < int(props.get('Frames',0)):
            raise ValueError('Search start must lie inside target media.')
        if props.get('Type') != 'Video + Audio' or not props.get('File Path') or not Path(props['File Path']).is_file():
            raise ValueError('Target must be online original video with embedded audio.')
        if reference.GetMediaPoolItem().GetUniqueId() == target_media_id:
            raise ValueError('Reference and target must be different recordings.')
        token = uuid.uuid4().hex
        if len(self.plans) >= 100:
            raise ValueError('Session plan limit reached.')
        self.plans[token] = {'project_id': project.GetUniqueId(), 'source_id': timeline_id,
                             'digest': self.edits.digest(timeline), 'target_id': target_media_id,
                             'reference_track': track, 'reference_ordinal': ordinal,
                             'mode': mode, 'state': 'previewed'}
        self.plans[token]['search_start_frame']=search_start_frame
        return {'plan_id': token, 'source': self.summary(timeline), 'reference_audio': reference.GetName(),
                'reference_start': reference.GetStart(), 'reference_end': reference.GetEnd(),
                'target': media.GetName(), 'target_bin': folder, 'mode': mode,'search_start_frame':search_start_frame,
                'operation': 'Duplicate source, append one linked video/audio pair on new tracks, native align.',
                'requires_review': True, 'warning': 'Resolve supplies no correlation confidence; a true API result is not proof of correct sync.'}

    def create(self, plan_id):
        plan = self.plans.get(plan_id)
        if not plan or plan['state'] != 'previewed':
            raise ValueError('Unknown/already attempted plan. Never repeat a timed-out write.')
        resolve, project = self.project()
        if project.GetUniqueId() != plan['project_id']:
            raise ValueError('Project changed; preview again.')
        source = self.find(project, plan['source_id'])
        if self.edits.digest(source) != plan['digest']:
            raise ValueError('Source changed; preview again.')
        media = self.media(project)[plan['target_id']][0]
        props = media.GetClipProperty() or {}
        if not props.get('File Path') or not Path(props['File Path']).is_file():
            raise ValueError('Target went offline; no copy created.')
        previous = project.GetCurrentTimeline()
        plan['state'] = 'attempted'
        result = {'plan_id': plan_id, 'requires_review': True}
        try:
            copy = source.DuplicateTimeline('MCP_SYNC_' + uuid.uuid4().hex[:12])
            if not copy or copy.GetUniqueId() == source.GetUniqueId():
                raise RuntimeError('Resolve did not create a fresh copy.')
            plan['copy_id'] = copy.GetUniqueId()
            result['timeline'] = self.summary(copy)
            if not project.SetCurrentTimeline(copy):
                raise RuntimeError('Could not select the copy; preserved for inspection.')
            baseline = self.edits.snapshot(copy)
            reference = copy.GetItemListInTrack('audio', plan['reference_track'])[plan['reference_ordinal']]
            if not copy.AddTrack('video') or not copy.AddTrack('audio', 'stereo'):
                raise RuntimeError('Could not add tracks; partial copy preserved.')
            video_index, audio_index = copy.GetTrackCount('video'), copy.GetTrackCount('audio')
            record = reference.GetStart()
            pair = []
            for media_type, index in ((1, video_index), (2, audio_index)):
                added = project.GetMediaPool().AppendToTimeline([{'mediaPoolItem': media,
                    'mediaType': media_type, 'trackIndex': index, 'recordFrame': record,
                    'startFrame':plan.get('search_start_frame',0),'endFrame':int(props['Frames'])}])
                if not added or len(added) != 1:
                    raise RuntimeError('Append failed; inspect partial copy before another attempt.')
                pair.extend(added)
            if not copy.SetClipsLinked(pair, True):
                raise RuntimeError('Could not link target audio/video; no alignment attempted.')
            before = [(c.GetStart(True), c.GetEnd(True)) for c in pair]
            source_before = [(c.GetSourceStartFrame(), c.GetSourceEndFrame()) for c in pair]
            sync_mode = (resolve.AUTO_ALIGN_CLIPS_USING_WAVEFORM if plan['mode'] == 'waveform'
                         else resolve.AUTO_ALIGN_CLIPS_USING_TIMECODE)
            selected = [reference] + list(reference.GetLinkedItems() or []) + pair
            selected = list({c.GetUniqueId(): c for c in selected}.values())
            ok = bool(copy.AutoAlignClips(selected, {'SyncUsing': sync_mode,
                        'UseTrack': resolve.AUTO_ALIGN_CLIPS_WAVEFORM_TRACK_AUTOMATIC}))
            after = [(c.GetStart(True), c.GetEnd(True)) for c in pair]
            source_after = [(c.GetSourceStartFrame(), c.GetSourceEndFrame()) for c in pair]
            current = self.edits.snapshot(copy)
            # New tracks may be interleaved by kind; match existing track keys explicitly.
            original_tracks = {(t['kind'], t['index']): t['items'] for t in current['tracks']}
            unchanged = all(original_tracks.get((t['kind'], t['index'])) == t['items'] for t in baseline['tracks'])
            linked = bool(pair[0].GetLinkedItems())
            result.update(assess_pair(before, after, source_before, source_after))
            result.update(native_success=ok, before=before, after=after,
                source_snapshot_unchanged=self.edits.digest(source) == plan['digest'],
                existing_copy_items_unchanged=unchanged, linked_pair=linked,
                alignment_verified=False)
            result['technical_checks_passed'] = (ok and unchanged and linked and
                result['source_snapshot_unchanged'] and result['paired_movement_matches'])
            result['ready_for_batch'] = False
            if not result['target_duration_preserved']:
                result['warning'] = 'Native alignment trimmed target boundaries on the copy. Review these limits before further processing; source files are untouched.'
            plan['state'] = 'completed'
            plan['result'] = result
            return result
        except Exception as exc:
            plan['error'] = str(exc)
            raise
        finally:
            result['original_selection_restored'] = bool(project.SetCurrentTimeline(previous)) if previous else None

    def status(self, plan_id):
        plan = self.plans.get(plan_id)
        if not plan:
            raise ValueError('Unknown session plan.')
        return {'state': plan['state'], 'copy_id': plan.get('copy_id'), 'result': plan.get('result'),
                'error': plan.get('error'),
                'retry_allowed': plan['state'] == 'previewed'}


class SectionSync:
    """Cut a continuous target using source-recording sections as timeline anchors."""
    def __init__(self, project, find, summary, edits):
        self.project, self.find, self.summary, self.edits = project, find, summary, edits
        self.plans = {}

    def media(self, project):
        return SyncBridge(self.project,self.find,self.summary,self.edits).media(project)

    @staticmethod
    def tc_seconds(tc):
        fields = tc.replace(';', ':').split(':')
        if len(fields) != 4 or not all(x.isdigit() for x in fields):
            raise ValueError('Recording timecode is missing or unsupported.')
        h, m, s, f = map(int, fields)
        if m >= 60 or s >= 60 or f >= 30:
            raise ValueError('Invalid recording timecode.')
        return h*3600 + m*60 + s + f/30

    def preview(self, timeline_id, target_id, anchor_timeline_id, target_anchor_item_id):
        _, p = self.project()
        source = self.find(p, timeline_id)
        anchor = self.find(p, anchor_timeline_id)
        rate = float(source.GetSetting('timelineFrameRate'))
        if float(anchor.GetSetting('timelineFrameRate')) != rate:
            raise ValueError('Anchor and source timeline frame rates differ.')
        target_entry = self.media(p).get(target_id)
        if not target_entry:
            raise ValueError('Target media is not in this project.')
        target, _ = target_entry
        target_props = target.GetClipProperty() or {}
        if target_props.get('Type') != 'Video + Audio' or not target_props.get('File Path') or not Path(target_props['File Path']).is_file():
            raise ValueError('Target must be online video with embedded audio.')
        target_anchor = next((c for i in range(1, anchor.GetTrackCount('video')+1)
                              for c in anchor.GetItemListInTrack('video', i) or []
                              if c.GetUniqueId()==target_anchor_item_id), None)
        if not target_anchor or not target_anchor.GetMediaPoolItem() or target_anchor.GetMediaPoolItem().GetUniqueId()!=target_id:
            raise ValueError('Target anchor item must be the aligned target clip in a reviewed sync copy.')
        anchor_record = source.GetStartFrame()
        ref_anchor = next((c for i in range(1,anchor.GetTrackCount('video')+1)
                           for c in anchor.GetItemListInTrack('video',i) or []
                           if c.GetStart(True)<=anchor_record<c.GetEnd(True)),None)
        if not ref_anchor:
            raise ValueError('No reference camera item at the calibration frame.')
        reference_media = ref_anchor.GetMediaPoolItem()
        if reference_media is None:
            raise ValueError('Calibration item has no source media.')
        target_src_anchor = target_anchor.GetSourceStartFrame() + round(anchor_record-target_anchor.GetStart(True))
        ref_src_anchor = ref_anchor.GetSourceStartFrame() + round(anchor_record-ref_anchor.GetStart(True))
        reference_tc = (reference_media.GetClipProperty() or {}).get('Start TC')
        reference_t0 = self.tc_seconds(reference_tc)
        offset = target_src_anchor - ref_src_anchor
        groups=[]
        for item in source.GetItemListInTrack('video',1) or []:
            media=item.GetMediaPoolItem()
            if not media: raise ValueError('Reference video contains a generator.')
            mid=media.GetUniqueId()
            ss,se=item.GetSourceStartFrame(),item.GetSourceEndFrame()
            record_start,record_end=round(item.GetStart(True)),round(item.GetEnd(True))
            if groups and groups[-1]['media_id']==mid and groups[-1]['record_end']==record_start and groups[-1]['source_end']==ss:
                groups[-1]['source_end']=se; groups[-1]['record_end']=record_end
            else:
                groups.append({'media_id':mid,'name':media.GetName(),'source_start':ss,'source_end':se,
                               'record_start':record_start,'record_end':record_end})
        if not groups: raise ValueError('Reference video track 1 is empty.')
        clips=[]
        for g in groups:
            m=self.media(p)[g['media_id']][0]
            props=m.GetClipProperty() or {}
            start_tc=props.get('Start TC')
            if not start_tc: raise ValueError('Reference media lacks a recording timecode.')
            delta=self.tc_seconds(start_tc)-reference_t0
            target_start=round(delta*rate+g['source_start']+offset)
            length=g['source_end']-g['source_start']
            target_end=target_start+length-1
            clips.append({**g,'target_start':target_start,'target_end':target_end,
                          'duration':length,'timecode_delta_seconds':round(delta,3),
                          'target_timeline_start':g['record_start']})
        frames=int(target_props.get('Frames') or 0)
        if any(c['target_start']<0 or c['target_end']>=frames for c in clips):
            raise ValueError('At least one derived target range falls outside its source; preview refused.')
        for a,b in zip(clips,clips[1:]):
            if a['target_end']>=b['target_start']:
                raise ValueError('Derived target source sections overlap; clock metadata is inconsistent.')
        token=uuid.uuid4().hex
        self.plans[token]={'project_id':p.GetUniqueId(),'source_id':timeline_id,
                           'digest':self.edits.digest(source),'target_id':target_id,
                           'clips':clips,'state':'previewed','rate':rate}
        return {'plan_id':token,'source_timeline':self.summary(source),'target':target.GetName(),
                'calibrated_by':'audio sync anchor plus reference-camera recording timecodes',
                'frame_offset_from_anchor':offset,'segments':clips,
                'preserves_reference_timeline_positions':True,
                'note':'Short camera recordings use their recorded start timecode after one audio-calibrated anchor. Verify camera clock accuracy and each cut.'}

    def create(self,plan_id):
        plan=self.plans.get(plan_id)
        if not plan or plan['state']!='previewed':
            raise ValueError('Unknown/already attempted plan; never retry a partial copy blindly.')
        _,p=self.project()
        if p.GetUniqueId()!=plan['project_id']: raise ValueError('Project changed; preview again.')
        source=self.find(p,plan['source_id'])
        if self.edits.digest(source)!=plan['digest']: raise ValueError('Reference timeline changed; preview again.')
        target=self.media(p)[plan['target_id']][0]
        prior=p.GetCurrentTimeline(); plan['state']='attempted'
        try:
            copy=source.DuplicateTimeline('MCP_SECTIONS_'+uuid.uuid4().hex[:10])
            if not copy or copy.GetUniqueId()==source.GetUniqueId(): raise RuntimeError('Resolve did not create a copy.')
            plan['copy_id']=copy.GetUniqueId()
            if not copy.AddTrack('video') or not copy.AddTrack('audio','stereo'):
                raise RuntimeError('Could not add target tracks; copy preserved.')
            vi,ai=copy.GetTrackCount('video'),copy.GetTrackCount('audio')
            copy.SetTrackName('video',vi,'Camera continuous — sections synchronized')
            copy.SetTrackName('audio',ai,'Camera continuous — reference audio')
            for typ,idx in ((1,vi),(2,ai)):
                info=[{'mediaPoolItem':target,'startFrame':c['target_start'],
                       'endFrame':c['target_end'],'mediaType':typ,'trackIndex':idx,
                       'recordFrame':c['target_timeline_start']} for c in plan['clips']]
                added=p.GetMediaPool().AppendToTimeline(info)
                if not added or len(added)!=len(plan['clips']):
                    raise RuntimeError('Some target sections could not be appended; inspect the preserved copy.')
            result={'plan_id':plan_id,'timeline':self.summary(copy),'copy_id':copy.GetUniqueId(),
                    'video_track':vi,'audio_track':ai,'segments':[]}
            for c in plan['clips']:
                match=next((x for x in copy.GetItemListInTrack('video',vi) or []
                            if x.GetMediaPoolItem() and x.GetMediaPoolItem().GetUniqueId()==plan['target_id']
                            and x.GetStart(True)==c['target_timeline_start']),None)
                amatch=next((x for x in copy.GetItemListInTrack('audio',ai) or []
                            if x.GetMediaPoolItem() and x.GetMediaPoolItem().GetUniqueId()==plan['target_id']
                            and x.GetStart(True)==c['target_timeline_start']),None)
                actual={'reference':c['name'],'expected_start':c['target_timeline_start'],
                        'video':None if match is None else {'start':match.GetStart(True),'end':match.GetEnd(True),'source_start':match.GetSourceStartFrame(),'source_end':match.GetSourceEndFrame()},
                        'audio':None if amatch is None else {'start':amatch.GetStart(True),'end':amatch.GetEnd(True),'source_start':amatch.GetSourceStartFrame(),'source_end':amatch.GetSourceEndFrame()}}
                actual['verified']=bool(match and amatch and actual['video']['start']==c['target_timeline_start']
                    and actual['audio']['start']==c['target_timeline_start']
                    and actual['video']['source_start']==actual['audio']['source_start'])
                result['segments'].append(actual)
            result['all_segments_verified']=all(x['verified'] for x in result['segments'])
            result['source_unchanged']=self.edits.digest(source)==plan['digest']
            plan.update(state='completed',result=result)
            return result
        except Exception as exc:
            plan['error']=str(exc);raise
        finally:
            if prior:p.SetCurrentTimeline(prior)

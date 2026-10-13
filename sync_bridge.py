"""Native Resolve alignment on fresh copies only; no source mutations or deletes."""
import uuid
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

    def preview(self, timeline_id, reference_audio_id, target_media_id, mode):
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
        return {'plan_id': token, 'source': self.summary(timeline), 'reference_audio': reference.GetName(),
                'reference_start': reference.GetStart(), 'reference_end': reference.GetEnd(),
                'target': media.GetName(), 'target_bin': folder, 'mode': mode,
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
                    'mediaType': media_type, 'trackIndex': index, 'recordFrame': record}])
                if not added or len(added) != 1:
                    raise RuntimeError('Append failed; inspect partial copy before another attempt.')
                pair.extend(added)
            if not copy.SetClipsLinked(pair, True):
                raise RuntimeError('Could not link target audio/video; no alignment attempted.')
            before = [(c.GetStart(True), c.GetEnd(True)) for c in pair]
            source_before = [(c.GetSourceStartFrame(), c.GetSourceEndFrame()) for c in pair]
            sync_mode = (resolve.AUTO_ALIGN_CLIPS_USING_WAVEFORM if plan['mode'] == 'waveform'
                         else resolve.AUTO_ALIGN_CLIPS_USING_TIMECODE)
            ok = bool(copy.AutoAlignClips([reference, pair[0], pair[1]], {'SyncUsing': sync_mode,
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

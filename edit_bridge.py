"""Session-local preview tokens and creation of fresh Resolve edit timelines."""
import hashlib
import json
import tempfile
import uuid
from pathlib import Path
import opentimelineio as otio
from timeline_edits import transform, structure, link_partitions, timestamp_frames


class EditBridge:
    def __init__(self, project, find, summary):
        self.project, self.find, self.summary = project, find, summary
        self.plans = {}
        self.results = {}
        self.work = Path(tempfile.mkdtemp(prefix='resolve-mcp-edits-'))

    def export(self, resolve, timeline):
        path = self.work / (uuid.uuid4().hex + '.otio')
        if not timeline.Export(str(path), resolve.EXPORT_OTIO):
            raise RuntimeError('Resolve could not export OTIO; no edit performed.')
        try:
            return otio.adapters.read_from_file(str(path))
        finally:
            path.unlink(missing_ok=True)

    def snapshot(self, timeline):
        data = {'summary': self.summary(timeline), 'markers': timeline.GetMarkers() or {}, 'tracks': []}
        for kind in ('video', 'audio', 'subtitle'):
            for index in range(1, timeline.GetTrackCount(kind) + 1):
                items = []
                for c in timeline.GetItemListInTrack(kind, index) or []:
                    media = c.GetMediaPoolItem()
                    items.append({'id': c.GetUniqueId(), 'start': c.GetStart(True), 'end': c.GetEnd(True),
                                  'source_start': c.GetSourceStartFrame(), 'source_end': c.GetSourceEndFrame(),
                                  'media': media.GetUniqueId() if media else None})
                data['tracks'].append({'kind': kind, 'index': index, 'items': items})
        return data

    def digest(self, timeline):
        return hashlib.sha256(json.dumps(self.snapshot(timeline), sort_keys=True).encode()).hexdigest()

    def check_source(self, timeline):
        if timeline.GetTrackCount('subtitle'):
            raise ValueError('Subtitle tracks are unsupported; prepare a raw audio/video timeline.')
        for kind in ('video', 'audio'):
            for index in range(1, timeline.GetTrackCount(kind) + 1):
                for clip in timeline.GetItemListInTrack(kind, index) or []:
                    media = clip.GetMediaPoolItem()
                    if media is None:
                        raise ValueError('Generators or clips without media are unsupported.')
                    props = media.GetClipProperty() or {}
                    if not props.get('File Path') or not Path(props['File Path']).is_file():
                        raise ValueError('Offline, nested or multicam media are unsupported.')
                    if any(word in str(props.get('Type', '')).lower() for word in ('multicam', 'compound', 'timeline')):
                        raise ValueError('Use original media rather than compound/multicam clips.')

    def preview(self, timeline_id, ranges, mode, origin_offset):
        resolve, project = self.project()
        timeline = self.find(project, timeline_id)
        self.check_source(timeline)
        if type(origin_offset) is not int or origin_offset < 0:
            raise ValueError('origin_offset must be a nonnegative integer frame offset.')
        source = self.export(resolve, timeline)
        rate = float(timeline.GetSetting('timelineFrameRate'))
        exact = []
        for r in ranges:
            if 'start_frame' in r and 'end_frame' in r:
                if type(r['start_frame']) is not int or type(r['end_frame']) is not int:
                    raise ValueError('Frame values must be integers.')
                a, b = r['start_frame'], r['end_frame']
            elif 'start' in r and 'end' in r:
                a, b = timestamp_frames(r['start'], rate), timestamp_frames(r['end'], rate)
            else:
                raise ValueError('Each range needs start/end elapsed timestamps or start_frame/end_frame.')
            exact.append({'start_frame': a + origin_offset, 'end_frame': b + origin_offset,
                          'label': r.get('label', '')})
        output, pieces, exact = transform(source, rate, exact, mode)
        token = uuid.uuid4().hex
        if len(self.plans) >= 20:
            raise ValueError('Too many session plans; restart the server to discard old previews.')
        self.plans[token] = {'project_id': project.GetUniqueId(), 'source_id': timeline_id,
                             'source_json': otio.adapters.write_to_string(source),
                             'digest': self.digest(timeline), 'rate': rate, 'output': output,
                             'start_tc': timeline.GetStartTimecode(), 'mode': mode,
                             'state': 'previewed'}
        return {'plan_id': token, 'mode': mode, 'fps': rate, 'end_is_exclusive': True,
                'source_timeline': self.summary(timeline), 'ranges': exact,
                'segments': pieces, 'source_unchanged': True,
                'warning': 'Raw audio/video only. OTIO does not guarantee preservation of grades, effects or automation.'}

    def create(self, plan_id, confirmation):
        plan = self.plans.get(plan_id)
        if plan is None or plan['state'] != 'previewed':
            raise ValueError('Unknown or already attempted plan; never retry an import blindly.')
        if plan['mode'] == 'keep_with_gaps' and confirmation != 'CONFIRM ' + plan_id:
            raise ValueError('Ask the human to approve omission of outside ranges, then pass CONFIRM <plan_id>.')
        resolve, project = self.project()
        if project.GetUniqueId() != plan['project_id']:
            raise ValueError('The open project changed; preview again.')
        source = self.find(project, plan['source_id'])
        if self.digest(source) != plan['digest']:
            raise ValueError('Source timing/media/markers changed; preview again.')
        self.check_source(source)
        # Re-export catches changes in OTIO metadata/effects since preview.
        current = self.export(resolve, source)
        # The stored fingerprint includes the original OTIO document below.
        if otio.adapters.write_to_string(current) != plan['source_json']:
            raise ValueError('Source export changed; preview again.')
        name = 'MCP_CUTS_' + uuid.uuid4().hex[:12]
        output = plan['output'].deepcopy()
        output.name = name
        path = self.work / (plan_id + '.otio')
        otio.adapters.write_to_file(output, str(path))
        previous = project.GetCurrentTimeline()
        plan['state'] = 'attempted'
        created = None
        restored = None
        response = {}
        try:
            created = project.GetMediaPool().ImportTimelineFromFile(str(path), {'timelineName': name})
            if not created or created.GetUniqueId() == source.GetUniqueId():
                raise RuntimeError('Import did not return a fresh timeline; inspect project before another attempt.')
            plan['result_id'] = created.GetUniqueId()
            if created.GetStartTimecode() != plan['start_tc'] and not created.SetStartTimecode(plan['start_tc']):
                raise RuntimeError('Could not preserve start timecode; the new timeline remains for inspection.')
            expected = structure(output, plan['rate'])
            self.results[(project.GetUniqueId(), created.GetUniqueId())] = {
                'expected': expected, 'rate': plan['rate'], 'source_id': plan['source_id'],
                'links': link_partitions(output), 'source_json': plan['source_json'],
                'source_digest': plan['digest'], 'start_frame': source.GetStartFrame(), 'plan_id': plan_id}
            result = self.verify(created.GetUniqueId())
            plan['state'] = 'created' if result['verified'] else 'verification_failed'
            response.update(result, timeline=self.summary(created), mode=plan['mode'])
            return response
        except Exception:
            # Preserve partial results; there is intentionally no deletion/rollback tool.
            raise
        finally:
            if previous:
                restored = bool(project.SetCurrentTimeline(previous))
            plan['original_selection_restored'] = restored
            response['original_selection_restored'] = restored
            path.unlink(missing_ok=True)

    def verify(self, timeline_id):
        resolve, project = self.project()
        result = self.results.get((project.GetUniqueId(), timeline_id))
        if result is None:
            raise ValueError('Verification is limited to outputs created in this server session.')
        timeline = self.find(project, timeline_id)
        exported = self.export(resolve, timeline)
        actual = structure(exported, result['rate'])
        source = self.find(project, result['source_id'])
        same = (self.digest(source) == result['source_digest'] and
                otio.adapters.write_to_string(self.export(resolve, source)) == result['source_json'])
        timing = actual == result['expected']
        start = timeline.GetStartFrame() == result['start_frame']
        links = link_partitions(exported) == result['links']
        fps = float(timeline.GetSetting('timelineFrameRate')) == result['rate']
        return {'timeline_id': timeline_id, 'plan_id': result['plan_id'],
                'verified': timing and same and start and links and fps, 'timing_media_tracks_match': timing,
                'linked_groups_match': links, 'fps_matches': fps,
                'start_frame_matches': start, 'source_snapshot_unchanged': same,
                'expected_clip_count': sum(len(t['clips']) for t in result['expected']),
                'actual_clip_count': sum(len(t['clips']) for t in actual),
                'scope': 'Frame positions, source in-points, media references and audio/video tracks; not visual effects.'}

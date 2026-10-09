import unittest
import opentimelineio as o
from timeline_edits import transform, structure, timestamp_frames


def fixture():
    t = o.schema.Timeline(global_start_time=o.opentime.RationalTime(86400, 24))
    for kind in ('Video', 'Audio'):
        track = o.schema.Track(kind=kind)
        track.append(o.schema.Clip(name='fixture',
            media_reference=o.schema.ExternalReference(target_url='file:///fixture.mov'),
            source_range=o.opentime.TimeRange(o.opentime.RationalTime(100, 24), o.opentime.RationalTime(240, 24)),
            metadata={'Resolve_OTIO': {'Link Group ID': 1}}))
        t.tracks.append(track)
    return t


class Cuts(unittest.TestCase):
    def test_split_preserves_every_frame_and_audio(self):
        source = fixture()
        before = o.adapters.write_to_string(source)
        result, _, _ = transform(source, 24, [{'start_frame': 24, 'end_frame': 72}], 'split_all')
        for track in structure(result, 24):
            self.assertEqual([(c[0], c[1], c[3]) for c in track['clips']], [(0,24,100),(24,72,124),(72,240,172)])
        self.assertEqual(before, o.adapters.write_to_string(source))
        self.assertEqual([c.metadata['Resolve_OTIO']['Link Group ID'] for c in result.tracks[0]],
                         [c.metadata['Resolve_OTIO']['Link Group ID'] for c in result.tracks[1]])

    def test_gaps_do_not_ripple(self):
        result, _, _ = transform(fixture(), 24, [{'start_frame':24,'end_frame':72},
                                               {'start_frame':120,'end_frame':144}], 'keep_with_gaps')
        for track in structure(result,24):
            self.assertEqual([(c[0],c[1],c[3]) for c in track['clips']], [(24,72,124),(120,144,220)])
        self.assertEqual(result.duration().value,240)

    def test_invalid_and_overlap(self):
        for ranges in ([{'start_frame':0,'end_frame':241}], [{'start_frame':True,'end_frame':10}],
                       [{'start_frame':0,'end_frame':20},{'start_frame':10,'end_frame':30}]):
            with self.assertRaises(ValueError): transform(fixture(),24,ranges,'split_all')

    def test_time(self):
        self.assertEqual(timestamp_frames('1:23',24),1992)
        self.assertEqual(timestamp_frames('0.5',29.97),15)
        for value in ('1:60','NaN','-1','01:00:00:00'):
            with self.assertRaises(ValueError): timestamp_frames(value,24)

    def test_retime_and_nested_rejected(self):
        t=fixture(); t.tracks[0][0].effects.append(o.schema.LinearTimeWarp(time_scalar=2))
        with self.assertRaises(ValueError): transform(t,24,[{'start_frame':0,'end_frame':24}],'split_all')
        t=fixture();t.tracks[0].append(o.schema.Stack())
        with self.assertRaises(ValueError): transform(t,24,[{'start_frame':0,'end_frame':24}],'split_all')


if __name__=='__main__': unittest.main()

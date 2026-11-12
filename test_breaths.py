import unittest

from montage_engine import breaths, passes
from montage_engine.model import Clip, Snapshot

# Measured in Lili V4.1 at 01:01:17:14 (after « eux »), the 30 frames before the cut, frame by frame:
# the voice, a silence, a soft breath, then the cut.
EUX = [-72.8, -65.2, -51.0, -48.9, -57.5, -55.9, -49.7, -59.9, -68.9, -68.0, -59.9, -60.1, -71.4, -74.3, -76.9, -77.0,
       -77.2, -76.5, -75.2, -74.5, -74.5, -73.6, -70.1, -68.9, -68.0, -67.6, -67.2, -68.6, -70.2, -73.2]


class BreathShapeTest(unittest.TestCase):
    def test_the_breath_after_eux_is_found(self):
        b = breaths.breath_before_cut(EUX)
        self.assertIsNotNone(b)
        self.assertEqual(b['after_voice'], 23)            # the voice ends 23 frames before the cut
        self.assertGreaterEqual(b['breath'], 5)

    def test_the_fading_end_of_a_word_is_not_a_breath(self):
        # the voice decays straight into the cut without a silence first
        self.assertIsNone(breaths.breath_before_cut([-77, -45, -44, -50, -58, -62, -66, -68, -70, -72]))

    def test_a_clean_cut_after_a_silence_is_not_a_breath(self):
        self.assertIsNone(breaths.breath_before_cut([-77, -45, -44, -50, -72, -76, -77, -77, -76, -77]))

    def test_speech_coming_back_is_not_a_breath(self):
        # silence then loud again: a new word, not a breath (that is a cut inside speech, another check)
        self.assertIsNone(breaths.breath_before_cut([-77, -45, -44, -76, -77, -68, -66, -46, -44, -45]))


class FindBreathsTest(unittest.TestCase):
    def snap(self):
        def c(start, end, file, src):
            return Clip(kind='audio', track=1, track_name='Audio 1', start=start, end=end, file=file, name=file, src_start=src)
        clips = [c(0, 100, 'a.mov', 0), c(100, 200, 'a.mov', 100),      # one take continuing: not a cut
                 c(200, 300, 'b.mov', 500)]
        s = Snapshot(clips=clips, out_frame=1000)
        s.track_names[('audio', 1)] = 'Audio 1'
        return s

    def test_only_real_cuts_are_examined_and_the_breath_is_reported_there(self):
        asked = []

        def levels(file, start_s, n, fps):
            asked.append((file, round(start_s * fps)))
            # a breath only before the end of the 'a' take (source frame 200)
            if file == 'a.mov' and round(start_s * fps) == 200 - n:
                return ([-77.0] * (n - len(EUX))) + EUX
            return [-77.0] * 3 + [-45.0] * (n - 3)               # speech up to the cut: no breath
        found = breaths.find_breaths(self.snap(), levels=levels)
        self.assertEqual([(f['frame'], f['side'], f['after_voice']) for f in found], [(200, 'end', 23)])
        self.assertNotIn(('a.mov', 100 - 36), asked)                # the inner join of the same take is not a cut

    def test_trims_cut_just_after_the_voice_and_run_through_the_generic_pass(self):
        edits = breaths.plan_breath_trims([dict(frame=200, side='end', breath=7, after_voice=23),
                                           dict(frame=300, side='start', breath=4, after_voice=10)])
        self.assertEqual([(e['P'], e['Q']) for e in edits], [(179, 200), (300, 308)])
        kept, new_end, fwd, back = passes.build_map(edits, 1000)
        self.assertEqual(new_end, 1000 - 21 - 8)


if __name__ == '__main__':
    unittest.main()

import unittest

from montage_engine import audio_rules, passes
from montage_engine.series import resolve_directives

BASE = dict(tracks=dict(pairs=[[1, 1], [2, 2]]))


def values(**sections):
    v = {k: dict(x) if isinstance(x, dict) else x for k, x in BASE.items()}
    for k, x in sections.items():
        v.setdefault(k, {}).update(x)
    return v


def w(f0, f1, text='mot'):
    return dict(f0=f0, f1=f1, w=text)


class PauseTrimTest(unittest.TestCase):
    def test_no_rule_plans_nothing(self):
        self.assertEqual(audio_rules.plan_pause_trims([w(0, 10), w(100, 110)], values()), [])
        self.assertEqual(audio_rules.plan_pause_trims([w(0, 10), w(100, 110)], values(cuts=dict(keep_pause_seconds=None))), [])

    def test_long_pauses_between_words_are_shortened_to_the_kept_length(self):
        words = [w(24, 40), w(42, 60), w(120, 140), w(150, 170)]          # pauses: 2, 60, 10 frames
        edits = audio_rules.plan_pause_trims(words, values(cuts=dict(keep_pause_seconds=0.5)))   # keep 12 frames
        self.assertEqual([(e['P'], e['Q'], e['D']) for e in edits], [(72, 120, 0)])
        self.assertIn('0.5', edits[0]['text'])

    def test_a_pause_just_over_the_kept_length_is_left_alone(self):
        words = [w(0, 10), w(23, 30)]                                     # 13 frames, keep 12: 1 frame to remove
        self.assertEqual(audio_rules.plan_pause_trims(words, values(cuts=dict(keep_pause_seconds=0.5))), [])

    def test_the_planned_edits_run_through_the_generic_pass(self):
        edits = audio_rules.plan_pause_trims([w(0, 50), w(150, 200)], values(cuts=dict(keep_pause_seconds=0.25)))
        kept, new_end, fwd, back = passes.build_map(edits, 400)
        self.assertEqual(new_end, 400 - (150 - 56))


class OverlayFilterTest(unittest.TestCase):
    def test_pauses_under_a_panel_or_b_roll_are_left_alone(self):
        from montage_engine.model import Clip, Snapshot
        from montage_engine.series import role_of
        panel = Clip(kind='video', track=4, track_name='Motion', start=100, end=200, file='p.mov', name='p.mov', src_start=0)
        cam = Clip(kind='video', track=1, track_name='Camera 1 (principale)', start=0, end=1000, file='c.mov', name='c.mov', src_start=0)
        s = Snapshot(clips=[panel, cam], out_frame=1000)
        edits = [dict(P=150, Q=180, D=0), dict(P=400, Q=450, D=0)]
        kept = audio_rules.outside_overlays(edits, s, lambda n: role_of(n).startswith('camera'))
        self.assertEqual([e['P'] for e in kept], [400])


class AudioLeadTest(unittest.TestCase):
    def timeline(self, second_src=100):
        import opentimelineio as otio
        R, TR = otio.opentime.RationalTime, otio.opentime.TimeRange

        def clip(name, src, dur):
            return otio.schema.Clip(name=name, media_reference=otio.schema.ExternalReference(
                target_url=f'/m/{name}', available_range=TR(R(0, 24), R(10000, 24))),
                source_range=TR(R(src, 24), R(dur, 24)))
        tl = otio.schema.Timeline('src')
        v = otio.schema.Track(name='Camera 1 (principale)', kind='Video')
        a = otio.schema.Track(name='Audio 1', kind='Audio')
        for t in (v, a):
            t.append(clip('a.mov', 0, 200))
            t.append(clip('b.mov', second_src, 200))
            tl.tracks.append(t)
        return tl

    def lengths(self, tl):
        return [round(c.source_range.duration.value) for c in tl.tracks[1]]

    def test_the_incoming_sound_starts_before_its_picture(self):
        tl = self.timeline()
        out, info = audio_rules.apply_audio_lead(tl, values(cuts=dict(audio_lead_frames=6)))
        self.assertEqual(info['moved'], [200])
        a, b = list(out.tracks[1])
        self.assertEqual(round(a.source_range.duration.value), 194)
        self.assertEqual((round(b.source_range.start_time.value), round(b.source_range.duration.value)), (94, 206))
        # pictures do not move, the voice track keeps its length, the source is untouched
        self.assertEqual([round(c.source_range.duration.value) for c in out.tracks[0]], [200, 200])
        self.assertEqual(round(out.tracks[1].duration().value), 400)
        self.assertEqual(self.lengths(tl), [200, 200])

    def test_a_cut_without_media_before_the_incoming_clip_is_left_alone(self):
        out, info = audio_rules.apply_audio_lead(self.timeline(second_src=2), values(cuts=dict(audio_lead_frames=6)))
        self.assertEqual((info['moved'], info['skipped']), ([], [200]))
        self.assertEqual(self.lengths(out), [200, 200])

    def test_no_rule_changes_nothing(self):
        out, info = audio_rules.apply_audio_lead(self.timeline(), values())
        self.assertEqual(info['moved'], [])
        self.assertEqual(self.lengths(out), [200, 200])


class LoudnessTest(unittest.TestCase):
    class Resolve:
        NORMALIZE_AUDIO_SET_LEVEL_RELATIVE = 'relative'

    class Timeline:
        def __init__(self, modes):
            self.modes, self.calls = modes, []

        def GetItemListInTrack(self, kind, i):
            return [f'{kind}{i}-a', f'{kind}{i}-b'] if i == 1 else []

        def GetNormalizeAudioModes(self):
            return self.modes

        def NormalizeAudioLevel(self, items, options):
            self.calls.append((items, options))
            return True

    def test_voice_clips_are_normalized_to_the_target_with_the_loudness_mode(self):
        tl = self.Timeline(['Sample Peak Program', 'ITU-R BS.1770-4'])
        rep = audio_rules.normalize_loudness(self.Resolve, tl, values(audio=dict(target_lufs=-14)))
        self.assertEqual(rep, dict(applied=True, mode='ITU-R BS.1770-4', target=-14, clips=2))
        items, options = tl.calls[0]
        self.assertEqual(items, ['audio1-a', 'audio1-b'])
        self.assertEqual((options['targetLoudness'], options['setLevelMode']), (-14.0, 'relative'))

    def test_nothing_is_changed_without_a_rule_or_a_loudness_mode(self):
        tl = self.Timeline(['Sample Peak Program'])
        self.assertEqual(audio_rules.normalize_loudness(self.Resolve, tl, values())['applied'], False)
        self.assertEqual(audio_rules.normalize_loudness(self.Resolve, tl, values(audio=dict(target_lufs=-14)))['reason'],
                         'no loudness mode in this Resolve version')
        self.assertEqual(tl.calls, [])

    def test_the_rules_merge_through_the_directive_levels(self):
        v, prov = resolve_directives(series=dict(audio=dict(target_lufs=-16), cuts=dict(audio_lead_frames=4)))
        self.assertEqual((v['audio']['target_lufs'], prov['audio.target_lufs']), (-16, 'series'))
        self.assertEqual(v['cuts']['silence_margin_db'], 8.0)          # the general cuts rules are kept


if __name__ == '__main__':
    unittest.main()

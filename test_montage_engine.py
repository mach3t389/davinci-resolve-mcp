import json
import tempfile
import unittest
from pathlib import Path

from montage_engine import anchors, checks, passes
from montage_engine.model import Clip, Snapshot
from montage_engine.series import (GENERAL, SERIES, VIDEO_TYPES, explain, missing_roles, overlay_params,
                                   resolve_directives, role_of)

CAM1, CAM2 = 'Camera 1 (principale)', 'Camera 2 (slider)'


def clip(kind, track, name, start, end, file='a.mov', src=0, enabled=True, cname=''):
    return Clip(kind=kind, track=track, track_name=name, start=start, end=end, file=file, name=cname or file,
                src_start=src, enabled=enabled)


def snap(clips, out=1000):
    s = Snapshot(name='t', out_frame=out, clips=clips)
    for c in clips:
        s.track_names[(c.kind, c.track)] = c.track_name
    s.track_names.setdefault(('video', 1), CAM2)
    s.track_names.setdefault(('video', 2), CAM1)
    return s


def directives(type_=None, series=None):
    return resolve_directives(type_=type_, series=series)[0]


class DirectivesTest(unittest.TestCase):
    def test_levels_override_only_what_they_state_and_keep_provenance(self):
        v, prov = resolve_directives(type_=VIDEO_TYPES['interview'], series=SERIES['masterclass'])
        self.assertEqual(v['cameras']['min_shot_seconds'], 2.5)
        self.assertEqual(prov['cameras.min_shot_seconds'], 'type')
        self.assertEqual(v['cameras']['main_share'], 0.75)
        self.assertEqual(prov['cameras.main_share'], 'series')
        self.assertEqual(prov['sync.tolerance_frames'], 'general')
        self.assertIn('cameras.main_share = 0.75  (series)', explain(v, prov))

    def test_masterclass_rules_are_not_general_defaults(self):
        v = directives()
        self.assertIsNone(v['cameras']['min_shot_seconds'])
        self.assertIsNone(v['cameras']['main_share'])
        self.assertNotIn('Visuels', v['overlays'])
        self.assertEqual(GENERAL['cameras']['min_shot_seconds'], None)

    def test_overlay_params_by_name_prefix(self):
        v = directives(series=SERIES['masterclass'])
        self.assertEqual(overlay_params(v, 'Visuels03_v001.mov')['fade_in_frames'], 32)
        self.assertEqual(overlay_params(v, 'Lili01_v002.mov')['fade_in_frames'], 12)

    def test_roles_come_from_names_not_numbers(self):
        self.assertEqual(role_of(CAM1), 'camera_main')
        self.assertEqual(role_of(CAM2), 'camera_secondary')
        self.assertEqual(role_of("Plans d'appui"), 'broll')
        self.assertEqual(role_of('Motion'), 'graphic')
        self.assertEqual(role_of('Panneaux Lili 2'), 'graphic')
        self.assertEqual(role_of('Stock Lili'), 'broll')
        self.assertEqual(role_of('Audio 1', 'audio'), 'voice')
        s = snap([clip('video', 3, 'Motion', 0, 10)])
        self.assertEqual(missing_roles(s), [])
        s.track_names = {('video', 1): 'Video 1'}
        self.assertEqual(missing_roles(s), ['camera_main', 'camera_secondary'])


class AnchorsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.media = self.tmp / 'C015.mov'
        self.media.write_bytes(b'x' * 3000)
        self.store = anchors.TranscriptStore(self.tmp / 'Transcriptions')
        # 24 fps media: word "bonjour" at 1.0 s (frame 24), "monde" at 2.0 s (frame 48)
        self.store.save(self.media, [{'w': 'bonjour', 's': 1.0, 'e': 1.5}, {'w': 'monde', 's': 2.0, 'e': 2.4}])

    def test_transcript_found_by_name_and_after_rename_by_fingerprint(self):
        self.assertIsNotNone(self.store.load(self.media))
        renamed = self.tmp / 'renamed.mov'
        self.media.rename(renamed)
        self.assertIsNotNone(self.store.load(renamed))
        other = self.tmp / 'other.mov'
        other.write_bytes(b'y' * 3000)
        self.assertIsNone(self.store.load(other))
        self.assertEqual(self.store.status([str(renamed), str(other)])['transcribed'], 1)

    def test_words_follow_the_source_after_a_cut(self):
        f = str(self.media)
        # the clip starts at source frame 20 and sits at timeline frame 100: "bonjour" (src 24) -> 104
        s = snap([clip('audio', 1, 'Audio 1', 100, 200, file=f, src=20)])
        words, missing = anchors.place_words(s, self.store)
        self.assertEqual([(w['w'], w['f0']) for w in words], [('bonjour', 104), ('monde', 128)])
        self.assertEqual(missing, [])
        # later version: 10 frames removed before it -> the words move, nothing is transcribed again
        s2 = snap([clip('audio', 1, 'Audio 1', 90, 190, file=f, src=20)])
        self.assertEqual(anchors.place_words(s2, self.store)[0][0]['f0'], 94)

    def test_source_without_transcript_is_reported_not_guessed(self):
        other = self.tmp / 'z.mov'
        other.write_bytes(b'q' * 100)
        s = snap([clip('audio', 1, 'Audio 1', 0, 50, file=str(other))])
        self.assertEqual(anchors.place_words(s, self.store), ([], [str(other)]))

    def test_markers_are_reanchored_by_source(self):
        f = 'a.mov'
        ref = snap([clip('audio', 1, 'Audio 1', 0, 100, file=f, src=1000)])
        new = snap([clip('audio', 1, 'Audio 1', 0, 40, file=f, src=1000),
                    clip('audio', 1, 'Audio 1', 60, 160, file=f, src=1040)])      # 0 frames lost, a 20-frame pause
        placed, unplaced = anchors.reanchor_markers(ref, {70: {'name': 'x'}}, new)
        self.assertEqual(list(placed), [90])      # source 1070 -> 60 + (1070 - 1040) = 90
        self.assertEqual(unplaced, [])
        gone = snap([clip('audio', 1, 'Audio 1', 0, 40, file=f, src=2000)])
        self.assertEqual(anchors.reanchor_markers(ref, {70: {}}, gone)[1], [70])

    def test_silent_ranges(self):
        words = [dict(f0=10, f1=20), dict(f0=22, f1=30), dict(f0=60, f1=70)]
        self.assertEqual(anchors.silent_ranges(words, 10, start=0, end=100), [(0, 10), (30, 60), (70, 100)])


class ChecksTest(unittest.TestCase):
    def two_cameras(self):
        return [clip('video', 1, CAM2, 0, 600, file='cam2.mov', src=0),
                clip('video', 2, CAM1, 0, 300, file='cam1.mov', src=0, enabled=True),
                clip('video', 2, CAM1, 300, 600, file='cam1.mov', src=300, enabled=False)]

    def test_sync_ok_and_image_from_another_take_is_an_error(self):
        v = directives()
        audio = clip('audio', 1, 'Audio 1', 0, 300, file='cam2.mov', src=0)
        ok = snap([audio, clip('video', 1, CAM2, 0, 300, file='cam2.mov', src=0)])
        self.assertEqual(checks.check_sync(ok, v), [])
        bad = snap([audio, clip('video', 1, CAM2, 0, 300, file='cam2.mov', src=48)])
        self.assertEqual(checks.check_sync(bad, v)[0]['check'], 'sync')
        empty = snap([audio, clip('video', 1, CAM2, 0, 200, file='cam2.mov', src=0)])
        self.assertEqual(len(checks.check_sync(empty, v)), 1)

    def test_visible_shots_merge_the_same_take_and_split_on_camera_change(self):
        s = snap(self.two_cameras())
        sh = checks.shots(s)
        self.assertEqual([(x['start'], x['end'], x['cam']) for x in sh], [(0, 300, 2), (300, 600, 1)])

    def test_min_shot_is_a_directive_not_a_default(self):
        s = snap([clip('video', 1, CAM2, 0, 30, file='a.mov'), clip('video', 1, CAM2, 30, 400, file='b.mov')], out=400)
        self.assertEqual(checks.check_min_shot(s, directives()), [])
        found = checks.check_min_shot(s, directives(type_=VIDEO_TYPES['interview']))
        self.assertEqual([f['frames'] for f in found], [30])

    def test_camera_share_against_the_series_target(self):
        v = directives(series=SERIES['masterclass'])
        self.assertEqual(checks.check_camera_share(snap(self.two_cameras(), 600), v)[0]['check'], 'camera_share')
        mostly_main = [clip('video', 1, CAM2, 0, 600, file='c2.mov'), clip('video', 2, CAM1, 0, 450, file='c1.mov'),
                       clip('video', 2, CAM1, 450, 600, file='c1.mov', src=450, enabled=False)]
        self.assertEqual(checks.check_camera_share(snap(mostly_main, 600), v), [])

    def test_same_camera_jump_cut_unless_hidden_by_an_overlay(self):
        v = directives()
        jump = [clip('video', 1, CAM2, 0, 600, file='c2.mov'), clip('video', 2, CAM1, 0, 200, file='c1.mov'),
                clip('video', 2, CAM1, 200, 600, file='c1.mov', src=900)]
        self.assertEqual([f['frame'] for f in checks.check_same_camera_jump_cuts(snap(jump, 600), v)], [200])
        covered = jump + [clip('video', 3, "Plans d'appui", 150, 260, file='b.mov')]
        self.assertEqual(checks.check_same_camera_jump_cuts(snap(covered, 600), v), [])

    def test_cut_inside_an_overlay_fade_is_flagged(self):
        v = directives()
        cams = [clip('video', 1, CAM2, 0, 600, file='c2.mov'), clip('video', 2, CAM1, 0, 200, file='c1.mov'),
                clip('video', 2, CAM1, 200, 600, file='c1.mov', src=900, enabled=False)]
        panel = clip('video', 4, 'Motion', 195, 300, file='Lili01_v001.mov', cname='Lili01_v001.mov')
        found = checks.check_cut_in_fade_zone(snap(cams + [panel], 600), v)
        self.assertEqual([f['frame'] for f in found], [200])
        far = clip('video', 4, 'Motion', 400, 500, file='Lili01_v001.mov', cname='Lili01_v001.mov')
        self.assertEqual(checks.check_cut_in_fade_zone(snap(cams + [far], 600), v), [])

    def test_broll_gaps_and_remnants(self):
        v = directives()
        gap = snap([clip('video', 3, "Plans d'appui", 0, 100, file='b1.mov'),
                    clip('video', 3, "Plans d'appui", 105, 200, file='b2.mov'),
                    clip('video', 3, "Plans d'appui", 400, 500, file='b3.mov')])
        self.assertEqual([f['frames'] for f in checks.check_broll_gaps(gap, v)], [5])
        rem = snap([clip('audio', 1, 'Audio 1', 0, 100, file='a.mov', src=0),
                    clip('audio', 1, 'Audio 1', 100, 102, file='a.mov', src=900),
                    clip('audio', 1, 'Audio 1', 102, 300, file='a.mov', src=2000)])
        self.assertEqual([(f['frame'], f['frames']) for f in checks.check_remnants(rem, v)], [(100, 2)])

    def test_cut_on_sound_with_a_fake_waveform(self):
        v = directives()
        s = snap([clip('audio', 1, 'Audio 1', 0, 100, file='a.mov', src=0),
                  clip('audio', 1, 'Audio 1', 100, 200, file='a.mov', src=500),
                  clip('audio', 1, 'Audio 1', 200, 300, file='a.mov', src=900)], out=300)

        def level(path, start_s, dur_s):                # speech -20 dB, silent at 0.0 s..., loud when mid-word
            return -20.0 if dur_s >= 3 or start_s > 20 else (-60.0 if start_s < 4 else -22.0)

        found = checks.check_cut_on_sound(s, v, level)
        self.assertEqual([f['frame'] for f in found], [100, 200])     # both joins land on loud audio
        quiet = checks.check_cut_on_sound(s, v, lambda path, start_s, dur_s: -20.0 if dur_s >= 3 else -60.0)
        self.assertEqual(quiet, [])

    def test_run_all_report(self):
        v = directives(type_=VIDEO_TYPES['interview'])
        rep = checks.run_all(snap(self.two_cameras(), 600), v)
        self.assertTrue(rep['ok'])
        self.assertEqual(rep['findings'], [])


class PassesTest(unittest.TestCase):
    def test_versions(self):
        names = ['Lili V3.8 - a', 'Lili V3.9 - b', 'Lili V4.1 - c', 'Lili V4.0 - d', 'Autre V9.9 - x', 'Lili brouillon']
        self.assertEqual(passes.latest_version(names, 'Lili'), 'Lili V4.1 - c')
        self.assertEqual(passes.next_version_name(names, 'Lili', 'cameras'), 'Lili V4.2 - cameras')
        self.assertEqual(passes.next_version_name([], 'Neuf', 'premiere'), 'Neuf V1.0 - premiere')

    def test_edit_map(self):
        edits = [dict(P=100, Q=150, D=0), dict(P=300, Q=300, D=24)]
        kept, new_end, fwd, back = passes.build_map(edits, 1000)
        self.assertEqual(new_end, 1000 - 50 + 24)
        self.assertEqual(fwd(99), 99)
        self.assertEqual(fwd(150), 100)
        self.assertEqual(fwd(120), 100)             # a removed frame lands where the cut closes
        self.assertEqual(fwd(300), 250 + 24)        # after the pause
        self.assertEqual(back(100), 150)
        self.assertIsNone(back(250))                # inside the inserted pause
        with self.assertRaises(ValueError):
            passes.build_map([dict(P=10, Q=50, D=0), dict(P=40, Q=60, D=0)], 100)

    def test_extend_to_silence(self):
        self.assertEqual(passes.extend_to_silence(-70, [-70] * 9, -65), 0)      # already silent
        self.assertEqual(passes.extend_to_silence(-30, [-30, -28, -66, -70, -70, -70, -70, -70, -70], -65), 2)
        self.assertIsNone(passes.extend_to_silence(-30, [-30] * 9, -65))
        self.assertEqual(passes.room_tone([[-60, -40], [-65, -50]]), -50)

    def otio_timeline(self):
        import opentimelineio as otio
        R = otio.opentime.RationalTime
        TR = otio.opentime.TimeRange
        tl = otio.schema.Timeline('src')

        def media_clip(name, src, dur):
            return otio.schema.Clip(name=name, media_reference=otio.schema.ExternalReference(target_url=f'/m/{name}'),
                                    source_range=TR(R(src, 24), R(dur, 24)))

        v_cam = otio.schema.Track(name='Camera 1 (principale)', kind='Video')
        v_cam.append(media_clip('cam.mov', 0, 400))
        a1 = otio.schema.Track(name='Audio 1', kind='Audio')
        a1.append(media_clip('cam.mov', 0, 400))
        v_gfx = otio.schema.Track(name='Motion', kind='Video')
        v_gfx.append(otio.schema.Gap(duration=R(250, 24)))
        v_gfx.append(media_clip('Lili01_v001.mov', 0, 100))
        v_gfx.append(otio.schema.Gap(duration=R(50, 24)))
        for t in (v_cam, a1, v_gfx):
            tl.tracks.append(t)
        return tl

    def test_otio_pass_removes_a_range_and_inserts_a_pause_on_every_track(self):
        tl = self.otio_timeline()
        edits = [dict(P=100, Q=150, D=0), dict(P=200, Q=200, D=48, panels=['Lili01_v001.mov'])]
        out, info = passes.transform_otio(tl, edits, 'dst')
        self.assertEqual(info['NEWEND'], 400 - 50 + 48)
        lens = {t.name: round(t.duration().rescaled_to(24).value) for t in out.tracks}
        self.assertEqual(set(lens.values()), {info['NEWEND']})
        cam = [c for c in out.tracks[0] if c.name == 'cam.mov']
        self.assertEqual([round(c.source_range.start_time.value) for c in cam], [0, 150, 200])
        self.assertEqual(round(sum(c.source_range.duration.value for c in cam)), 350)
        # the panel starts exactly where its pause starts (old 200 -> new 150)
        pos = 0
        for item in out.tracks[2]:
            if item.name == 'Lili01_v001.mov':
                break
            pos += round(item.duration().rescaled_to(24).value)
        self.assertEqual(pos, 150)
        self.assertEqual(round(tl.duration().value), 400)      # the source timeline is untouched

    def test_otio_pass_refuses_transitions_on_cut_tracks(self):
        import opentimelineio as otio
        tl = self.otio_timeline()
        tl.tracks[0].insert(1, otio.schema.Transition(name='x', in_offset=otio.opentime.RationalTime(1, 24),
                                                      out_offset=otio.opentime.RationalTime(1, 24)))
        with self.assertRaises(ValueError):
            passes.transform_otio(tl, [dict(P=10, Q=20, D=0)], 'dst')


if __name__ == '__main__':
    unittest.main()

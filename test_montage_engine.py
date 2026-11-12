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

    def test_parked_material_after_the_end_of_the_edit_is_ignored(self):
        f = str(self.media)
        s = snap([clip('audio', 1, 'Audio 1', 0, 100, file=f, src=20), clip('audio', 1, 'Audio 1', 5000, 5100, file='/parked/x.mov')], out=1000)
        words, missing = anchors.place_words(s, self.store)
        self.assertEqual((len(words), missing), (2, []))              # the parked clip has no transcript and is not reported

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

    def test_hallucinated_credits_are_removed_and_listed_but_real_speech_stays(self):
        w = lambda t, s: {'w': t, 's': s, 'e': s + 0.3}
        words = [w('Oui', 0), w('Sous-titrage', 1), w('FR', 1.4), w('?', 1.7), w('mais', 2), w('le', 3), w('sous-titrage', 3.2),
                 w('français', 3.6), w('Merci', 5), w("d'avoir", 5.2), w('regardé', 5.4), w('cette', 5.6), w('vidéo.', 5.8)]
        clean, removed = anchors.strip_hallucinations(words)
        self.assertEqual([x['w'] for x in clean], ['Oui', 'mais', 'le', 'sous-titrage', 'français'])
        self.assertEqual([r['text'] for r in removed], ['Sous-titrage FR ?', "Merci d'avoir regardé cette vidéo."])
        self.assertEqual(removed[0]['s'], 1)

    def test_store_cleans_on_save_and_on_demand_but_not_a_file_being_written(self):
        import os
        import time
        bad = [{'w': 'Bonjour', 's': 0.0, 'e': 0.5}, {'w': 'Sous-titres', 's': 1.0, 'e': 1.3},
               {'w': 'réalisés', 's': 1.3, 'e': 1.5}, {'w': 'par', 's': 1.5, 'e': 1.6}, {'w': 'la', 's': 1.6, 'e': 1.7},
               {'w': 'communauté', 's': 1.7, 'e': 2.0}, {'w': "d'Amara.org", 's': 2.0, 'e': 2.5}]
        data = self.store.save(self.media, bad)
        self.assertEqual([x['w'] for x in data['words']], ['Bonjour'])
        self.assertEqual(len(data['removed_phrases']), 1)
        # a file saved before the filter existed: cleaned on demand once it is old enough
        path = self.store._path(self.media)
        raw = json.loads(path.read_text(encoding='utf8'))
        raw['words'] = [{'w': 'Sous-titrage', 's': 1.0, 'e': 1.2}, {'w': 'FR', 's': 1.2, 'e': 1.4}, {'w': 'ok', 's': 2.0, 'e': 2.2}]
        raw.pop('removed_phrases')
        path.write_text(json.dumps(raw), encoding='utf8')
        self.assertEqual(self.store.clean(min_age_seconds=3600), {})                    # too recent: left alone
        old = time.time() - 7200
        os.utime(path, (old, old))
        self.assertEqual(self.store.clean(min_age_seconds=3600), {path.name: ['Sous-titrage FR']})
        self.assertEqual([x['w'] for x in json.loads(path.read_text(encoding='utf8'))['words']], ['ok'])

    def test_unaligned_words_and_segments_keep_their_text(self):
        from types import SimpleNamespace as NS
        from montage_engine import transcribe_source as ts
        ww = lambda t, s, e: NS(word=' ' + t, start=s, end=e)
        whisper = [NS(start=0.0, end=3.0, words=[ww('En', 0.0, 0.2), ww('2026', 0.3, 0.9), ww('elle-même', 0.95, 1.3), ww('oui', 1.4, 1.6)]),
                   NS(start=4.0, end=6.0, words=[ww('Je', 4.0, 4.2), ww('reprends', 4.3, 4.9)])]
        aligned = [{'words': [{'word': 'En', 'start': 0.05, 'end': 0.25}, {'word': '2026'},
                              {'word': 'oui', 'start': 1.45, 'end': 1.65}]},      # the aligner dropped "elle-même"
                   {'words': []}]                                                 # and failed on the second segment
        words, stats = ts.merge_aligned(whisper, aligned)
        self.assertEqual([w['w'] for w in words], ['En', '2026', 'elle-même', 'oui', 'Je', 'reprends'])    # nothing lost
        self.assertEqual((words[0]['s'], words[3]['s']), (0.05, 1.45))          # aligner's times where it placed the word
        self.assertEqual(words[2]['s'], 0.95)                                    # Whisper's time for the dropped word
        self.assertEqual(words[4]['s'], 4.0)                                     # Whisper's time for the failed segment
        self.assertEqual(stats, dict(words_whisper_timing=3))
        w2, s2 = ts.merge_aligned(whisper, aligned[:1])                          # different segmentation: same result
        self.assertEqual((len(w2), s2['words_whisper_timing']), (6, 3))
        w3, s3 = ts.merge_aligned(whisper, [])                                   # no alignment at all
        self.assertEqual((len(w3), s3['words_whisper_timing']), (6, 6))

    def test_alignment_pairs_words_over_the_file_when_segmentations_differ_and_apostrophes_are_split(self):
        from types import SimpleNamespace as NS
        from montage_engine import transcribe_source as ts
        ww = lambda t, s, e: NS(word=' ' + t, start=s, end=e)
        # Whisper: one segment, "J" + "'ai" split; the aligner: two sentences with "J'ai" whole
        whisper = [NS(start=0.0, end=5.0, words=[ww('J', 0.0, 0.1), ww("'ai", 0.1, 0.3), ww('mangé.', 0.4, 0.9), ww('Puis', 2.0, 2.3),
                                                  ww('m', 2.4, 2.5), ww("'est", 2.5, 2.7), ww('venu.', 2.8, 3.1)])]
        aligned = [{'start': 0.0, 'end': 1.0, 'words': [{'word': "J'ai", 'start': 0.12, 'end': 0.35}, {'word': 'mangé.', 'start': 0.45, 'end': 0.95}]},
                   {'start': 2.0, 'end': 3.2, 'words': [{'word': 'Puis', 'start': 2.05, 'end': 2.35}, {'word': "m'est", 'start': 2.45, 'end': 2.75},
                                                        {'word': 'venu.', 'start': 2.85, 'end': 3.15}]}]
        words, stats = ts.merge_aligned(whisper, aligned)
        self.assertEqual([w['w'] for w in words], ["J'ai", 'mangé.', 'Puis', "m'est", 'venu.'])
        self.assertEqual([w['s'] for w in words], [0.12, 0.45, 2.05, 2.45, 2.85])
        self.assertEqual(stats, dict(words_whisper_timing=0))

    def test_untimed_aligned_words_are_timed_between_their_neighbours(self):
        from montage_engine import transcribe_source as ts
        got = ts.complete_words([{'word': 'En', 'start': 0.1, 'end': 0.3}, {'word': '2026'},
                                 {'word': 'oui', 'start': 1.0, 'end': 1.2}], 0.0, 2.0)
        self.assertEqual((got[1]['s'], got[1]['e']), (0.3, 1.0))

    def test_a_whisper_loop_is_cut_back_and_listed_but_real_repeats_stay(self):
        w = lambda t, s: {'w': t, 's': s, 'e': s + 0.2}
        loop = [w(x, 10 + k * 0.2) for k, x in enumerate(['plus', 'loin,'] * 40)]
        words = [w('On', 0), w('va', 0.3)] + loop + [w('Tu', 30), w('peux', 30.3)]
        clean, removed = anchors.collapse_loops(words)
        self.assertEqual([x['w'] for x in clean], ['On', 'va'] + ['plus', 'loin,'] * 3 + ['Tu', 'peux'])
        self.assertEqual(len(removed), 1)
        self.assertEqual((removed[0]['kind'], removed[0]['repeats'], removed[0]['text']), ('loop', 40, 'plus loin,'))
        self.assertGreater(removed[0]['e'] - removed[0]['s'], 10)
        real = [w(x, k * 0.3) for k, x in enumerate(['Non,', 'non,', 'non,', 'non,', 'non.', 'Oui'])]      # 5 repeats: kept
        self.assertEqual(len(anchors.collapse_loops(real)[0]), 6)

    def test_clean_words_removes_credits_and_loops_and_labels_them(self):
        w = lambda t, s: {'w': t, 's': s, 'e': s + 0.2}
        words = ([w('Oui', 0), w('Sous-titrage', 1), w('FR', 1.3)] + [w('plus', 2 + k * 0.2) for k in range(9)]
                 + [w('fin', 9)])
        clean, removed = anchors.clean_words(words)
        self.assertEqual([x['kind'] for x in removed], ['credit', 'loop'])
        self.assertEqual([x['w'] for x in clean], ['Oui', 'plus', 'plus', 'plus', 'fin'])

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

    def test_silence_extension_plan_uses_waveform_and_real_cuts_only(self):
        v = directives()
        cams = [clip('video', 1, CAM2, 0, 100, file='c2.mov'), clip('video', 1, CAM2, 100, 300, file='c2.mov', src=500),
                clip('video', 1, CAM2, 300, 500, file='c2.mov', src=900),
                clip('video', 2, CAM1, 0, 100, file='c1.mov'), clip('video', 2, CAM1, 100, 300, file='c1.mov', src=500),
                clip('video', 2, CAM1, 300, 500, file='c1.mov', src=900, enabled=False)]
        audio = [clip('audio', 1, 'Audio 1', 0, 100, file='a.mov', src=0),
                 clip('audio', 1, 'Audio 1', 100, 300, file='a.mov', src=500),
                 clip('audio', 1, 'Audio 1', 300, 500, file='a.mov', src=900)]
        s = snap(cams + audio, out=500)

        def level(path, start_s, dur_s):
            # first cut: sound for 2 more frames then silent; second cut: loud for 3 s (no silence)
            end1, end2 = 100 / 24, (500 + 200) / 24
            if abs(start_s - (end1 - 2 / 24)) < 1e-6: return -20.0
            if abs(start_s - end1) < 1e-6 or abs(start_s - (end1 + 1 / 24)) < 1e-6: return -22.0
            if start_s > end1 and start_s < end1 + 1: return -70.0
            if start_s > end2 - 1: return -20.0
            return -65.0

        edits, unresolved = passes.plan_silence_extensions(s, v, level)
        self.assertEqual([(e['P'], e['D'], e['extend']) for e in edits], [(100, 2, True)])
        self.assertEqual([u[0] for u in unresolved], [300])

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

    def test_extend_moves_the_cut_to_the_silence_and_ripples(self):
        tl = self.otio_timeline()
        out, info = passes.transform_otio(tl, [dict(P=100, Q=100, D=6, extend=True)], 'dst')
        self.assertEqual(info['NEWEND'], 406)
        cam = [c for c in out.tracks[0] if c.name == 'cam.mov']
        self.assertEqual([round(c.source_range.duration.value) for c in cam], [106, 300])   # first piece +6 frames
        self.assertEqual([round(c.source_range.start_time.value) for c in cam], [0, 100])
        lens = {round(t.duration().rescaled_to(24).value) for t in out.tracks}
        self.assertEqual(lens, {406})

    def test_otio_pass_refuses_transitions_on_cut_tracks(self):
        import opentimelineio as otio
        tl = self.otio_timeline()
        tl.tracks[0].insert(1, otio.schema.Transition(name='x', in_offset=otio.opentime.RationalTime(1, 24),
                                                      out_offset=otio.opentime.RationalTime(1, 24)))
        with self.assertRaises(ValueError):
            passes.transform_otio(tl, [dict(P=10, Q=20, D=0)], 'dst')


if __name__ == '__main__':
    unittest.main()

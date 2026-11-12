import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from montage_engine import cli, rushflow_rules as rr

# What Rushflow's get_montage_directives returns for the Masterclass project (shape of readMontageDirectives).
RESULT = {
    'layers': {'general': {'stored': False, 'layer': {}}, 'series': {'stored': True, 'layer': {'cameras': {'main_share': 0.75}}}},
    'values': {
        'sync': {'tolerance_frames': 1},
        'cuts': {'silence_max_extend_frames': 8, 'silence_margin_db': 8.0, 'remnant_max_frames': 3, 'wave_suspect_margin_db': 6.0,
                 'wave_probe_frames': 2, 'keep_pause_seconds': 0.5, 'audio_lead_frames': 6},
        'cameras': {'switch_on_jump_cut': True, 'min_shot_seconds': 2.5, 'main_share': 0.75, 'share_tolerance': 0.08,
                    'max_same_camera_seconds': 10},
        'overlays': {'default': {'fade_in_frames': 12, 'fade_out_frames': 18, 'pad_frames': 12}, 'Visuels': {'fade_in_frames': 32}},
        'audio': {'target_lufs': -14, 'music_under_voice_db': 12},
        'tracks': {'pairs': [[1, 1], [2, 2]]},
        'mystery': {'knob': 3},
    },
    'provenance': {'cameras.main_share': 'series', 'cameras.min_shot_seconds': 'type', 'audio.target_lufs': 'general',
                   'audio.music_under_voice_db': 'series'},
    'instructions': [{'level': 'series', 'text': "Dans les intros, coupe plus vite.\nQuand l'invitée rit, reste sur elle."}, {'level': 'general', 'text': '  '}],
    'types': [], 'catalog': [],
}


class RushflowRulesTest(unittest.TestCase):
    def test_reads_a_get_montage_directives_result_from_a_dict_a_string_and_a_file(self):
        a = rr.load_rules(RESULT)
        self.assertEqual(a['values']['cameras']['main_share'], 0.75)
        self.assertEqual(a['provenance']['cameras.main_share'], 'series')
        self.assertEqual(rr.load_rules(json.dumps(RESULT))['values'], a['values'])
        path = Path(tempfile.mkdtemp()) / 'rules.json'
        path.write_text(json.dumps({'directives': RESULT}), encoding='utf8')                      # wrapped, as in a job
        self.assertEqual(rr.load_rules(str(path))['values'], a['values'])

    def test_missing_rules_take_the_engines_default_and_it_is_said(self):
        out = rr.load_rules({'values': {'cameras': {'min_shot_seconds': 2.5}}})
        self.assertEqual(out['values']['tracks']['pairs'], [[1, 1], [2, 2]])             # the checks index these directly
        self.assertEqual(out['values']['cameras']['min_shot_seconds'], 2.5)               # Rushflow's value is kept
        self.assertIn('tracks.pairs', out['filled'])
        self.assertNotIn('cameras.min_shot_seconds', out['filled'])

    def test_instructions_are_listed_for_the_assistant_never_interpreted(self):
        out = rr.load_rules(RESULT)
        self.assertEqual([t['level'] for t in out['instructions']], ['series'])           # the blank one is dropped
        self.assertIn('coupe plus vite', out['instructions'][0]['text'])

    def test_each_rule_says_what_the_engine_does_with_it(self):
        out = rr.load_rules(RESULT)
        rows = {r['rule']: r for r in rr.describe_rules(out['values'], out['provenance'])}
        self.assertEqual(rows['cameras.main_share']['status'], rr.APPLIED)
        self.assertEqual(rows['cameras.main_share']['level'], 'series')
        self.assertEqual(rows['cameras.max_same_camera_seconds']['status'], rr.INFO)
        self.assertEqual(rows['audio.music_under_voice_db']['status'], rr.ASSISTANT)
        self.assertEqual(rows['cameras.switch_on_jump_cut']['status'], rr.UNREAD)
        self.assertEqual(rows['mystery.knob']['status'], rr.UNKNOWN)                      # never dropped silently
        self.assertEqual(rows['overlays.Visuels.fade_in_frames']['status'], rr.APPLIED) if 'overlays.Visuels.fade_in_frames' in rows else None
        self.assertTrue(any(k.startswith('overlays') and v['status'] == rr.APPLIED for k, v in rows.items()))

    def test_rules_left_empty_are_not_rules(self):
        rows = rr.describe_rules({'cameras': {'main_share': None, 'min_shot_seconds': 2.5}})
        self.assertEqual([r['rule'] for r in rows], ['cameras.min_shot_seconds'])

    def test_unapplied_rules_come_last_and_are_counted(self):
        out = rr.load_rules(RESULT)
        rows = rr.describe_rules(out['values'], out['provenance'])
        statuses = [r['status'] for r in rows]
        self.assertEqual(statuses, sorted(statuses, key=[rr.APPLIED, rr.INFO, rr.ASSISTANT, rr.UNREAD, rr.UNKNOWN].index))
        counts = rr.summary(rows)
        self.assertEqual(counts[rr.UNKNOWN], 1)
        self.assertGreaterEqual(counts[rr.APPLIED], 10)

    def test_bad_input_is_refused_with_a_clear_message(self):
        for bad, msg in (([], 'JSON object'), ({'nothing': 1}, 'values'), ('{not json', 'valid JSON'), ('/nonexistent/rules.json', 'cannot read')):
            with self.assertRaises(rr.RulesError) as cm:
                rr.load_rules(bad)
            self.assertIn(msg, str(cm.exception))

    def test_the_loaded_values_drive_the_checks(self):
        from montage_engine import checks
        from montage_engine.model import Clip, Snapshot
        out = rr.load_rules({'values': {'cameras': {'min_shot_seconds': 2.5}}})
        s = Snapshot(out_frame=400)
        s.track_names[('video', 1)] = 'Camera 2 (slider)'
        s.clips = [Clip('video', 1, 'Camera 2 (slider)', 0, 30, file='a.mov'), Clip('video', 1, 'Camera 2 (slider)', 30, 400, file='b.mov')]
        self.assertEqual([f['frames'] for f in checks.check_min_shot(s, out['values'])], [30])
        none = rr.load_rules({'values': {'cameras': {'min_shot_seconds': None}}})
        self.assertEqual(checks.check_min_shot(s, none['values']), [])

    def test_cli_takes_either_rules_or_type_and_series_not_both(self):
        path = Path(tempfile.mkdtemp()) / 'rules.json'
        path.write_text(json.dumps(RESULT), encoding='utf8')
        values, prov, rules = cli._rules(Namespace(rules=str(path), type=None, series=None))
        self.assertEqual(values['cameras']['main_share'], 0.75)
        self.assertIsNotNone(rules)
        with self.assertRaises(SystemExit):
            cli._rules(Namespace(rules=str(path), type='interview', series=None))
        values2, _, rules2 = cli._rules(Namespace(rules=None, type='interview', series='masterclass'))
        self.assertIsNone(rules2)
        self.assertEqual(values2['cameras']['main_share'], 0.75)
        with self.assertRaises(SystemExit):
            cli._rules(Namespace(rules=str(Path(tempfile.mkdtemp()) / 'missing.json'), type=None, series=None))


if __name__ == '__main__':
    unittest.main()

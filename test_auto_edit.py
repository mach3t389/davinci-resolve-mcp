import unittest

from auto_edit import (TREATMENTS, RecipeError, catalog, compare_cuts, number, plan_edit, reference_keeps,
                       timecode_seconds, treatment, validate_recipe, words_from_resolve, words_from_whisper)

# "bonjour tout le monde" 2-4 s, pause, "merci d'être là" 7-9 s, pause, "au revoir" 12-13 s, end at 15 s
WORDS = [
    dict(start=2.0, end=2.5, text='bonjour'), dict(start=2.5, end=3.0, text='tout'), dict(start=3.0, end=3.4, text='le'), dict(start=3.4, end=4.0, text='monde'),
    dict(start=7.0, end=7.5, text='merci'), dict(start=7.5, end=8.0, text="d'être"), dict(start=8.0, end=9.0, text='là'),
    dict(start=12.0, end=12.4, text='au'), dict(start=12.4, end=13.0, text='revoir'),
]
MAIN = dict(duration=15, fps=24, words=WORDS)
SOURCES = {'main': MAIN, 'intro.mov': dict(duration=2, fps=24), 'fin.mov': dict(duration=3, fps=24), 'broll.mov': dict(duration=4, fps=24)}


def main_clip(*treatments, **extra):
    return dict(id='main', label='Vidéo principale', source=dict(type='field', field='main'), treatments=list(treatments), **extra)


def file_clip(clip_id, file, **extra):
    return dict(id=clip_id, source=dict(type='file', file=file), **extra)


def recipe(*clips, **extra):
    return dict(version=2, clips=list(clips), **extra)


def order(plan):
    return [item['clip'] for item in plan['sequence']]


class Recipes(unittest.TestCase):
    def test_refuses_anything_unknown_instead_of_ignoring_it(self):
        with self.assertRaisesRegex(RecipeError, 'unknown step'):
            validate_recipe(recipe(main_clip(dict(type='logo'))))
        with self.assertRaisesRegex(RecipeError, 'unknown setting'):
            validate_recipe(recipe(main_clip(dict(type='silence', params=dict(nope=1)))))
        with self.assertRaisesRegex(RecipeError, 'version'):
            validate_recipe(dict(version=99, clips=[]))
        with self.assertRaisesRegex(RecipeError, 'shared setting'):
            validate_recipe(recipe(main_clip(), settings=dict(logo=dict(size=1))))

    def test_refuses_an_empty_recipe_a_bad_source_and_duplicate_clips(self):
        with self.assertRaisesRegex(RecipeError, 'at least one clip'):
            validate_recipe(recipe())
        with self.assertRaisesRegex(RecipeError, 'source'):
            validate_recipe(recipe(dict(id='a', source=dict(type='file'))))
        with self.assertRaisesRegex(RecipeError, 'duplicate'):
            validate_recipe(recipe(file_clip('a', 'x'), file_clip('a', 'y')))

    def test_refuses_values_out_of_range(self):
        with self.assertRaisesRegex(RecipeError, 'below'):
            validate_recipe(recipe(main_clip(dict(type='silence', params=dict(min_pause_seconds=0)))))
        with self.assertRaisesRegex(RecipeError, 'must be a number'):
            validate_recipe(recipe(main_clip(dict(type='silence', params=dict(min_pause_seconds='long')))))

    def test_catalog_publishes_treatments_and_finishing_steps(self):
        published = catalog()
        treatments = {t['type']: t for t in published['treatments']}
        self.assertLessEqual({'trim', 'silence', 'place_clip'}, set(treatments))
        self.assertIn('min_pause_seconds', treatments['silence']['params'])
        self.assertLessEqual({'fade', 'normalize', 'instruction'}, {f['type'] for f in published['finishes']})
        self.assertEqual(published['sources'], ['file', 'field'])

    def test_a_new_treatment_needs_no_change_elsewhere(self):
        @treatment('test_only', 1, 'Test', dict(level=number(1, 0, 5)))
        def apply(plan, params):
            plan.flags.append(dict(severity='green', reason='test', level=params['level']))
        try:
            self.assertEqual(plan_edit(recipe(main_clip(dict(type='test_only'))), SOURCES)['recipe_version'], 2)
            self.assertIn('test_only', [t['type'] for t in catalog()['treatments']])
        finally:
            del TREATMENTS['test_only']


class Sequence(unittest.TestCase):
    def test_the_list_order_is_the_edit_order(self):
        plan = plan_edit(recipe(file_clip('fin', 'fin.mov'), main_clip(), file_clip('intro', 'intro.mov')), SOURCES)
        self.assertEqual(order(plan), ['fin', 'main', 'intro'])
        self.assertEqual([item['final_start'] for item in plan['sequence']], [0, 3, 18])
        self.assertEqual(plan['final_duration'], 20)

    def test_a_client_clip_can_go_before_the_introduction(self):
        client = dict(id='client', source=dict(type='field', field='opening'), optional=True, treatments=[dict(type='trim', params=dict(range_field='opening_range', precision='exact'))])
        sources = dict(SOURCES, opening=dict(duration=20, fps=24))
        plan = plan_edit(recipe(client, file_clip('intro', 'intro.mov'), main_clip(), file_clip('fin', 'fin.mov')), sources, dict(opening_range=dict(startSeconds=5, endSeconds=11)))
        self.assertEqual(order(plan), ['client', 'intro', 'main', 'fin'])
        self.assertEqual(plan['clips']['client']['keeps'], [[5.0, 11.0]])
        self.assertEqual(plan['final_duration'], 6 + 2 + 15 + 3)

    def test_an_optional_clip_not_provided_is_skipped(self):
        client = dict(id='client', source=dict(type='field', field='opening'), optional=True)
        plan = plan_edit(recipe(client, file_clip('intro', 'intro.mov'), main_clip()), SOURCES)
        self.assertEqual(order(plan), ['intro', 'main'])
        self.assertEqual(plan['skipped'], [dict(clip='client', reason='not_provided')])

    def test_a_required_video_that_is_missing_stops_the_plan(self):
        with self.assertRaisesRegex(RecipeError, 'no video for'):
            plan_edit(recipe(dict(id='client', source=dict(type='field', field='opening'))), SOURCES)

    def test_a_clip_can_depend_on_a_form_answer_or_be_disabled(self):
        fin = file_clip('fin', 'fin.mov', when=dict(field='conclusion', equals=True))
        self.assertEqual(order(plan_edit(recipe(main_clip(), fin), SOURCES, dict(conclusion=False))), ['main'])
        self.assertEqual(order(plan_edit(recipe(main_clip(), fin), SOURCES, dict(conclusion=True))), ['main', 'fin'])
        self.assertEqual(order(plan_edit(recipe(main_clip(), dict(fin, enabled=False)), SOURCES, dict(conclusion=True))), ['main'])

    def test_flags_clips_with_different_frame_rates(self):
        sources = dict(SOURCES, **{'intro.mov': dict(duration=2, fps=30)})
        plan = plan_edit(recipe(file_clip('intro', 'intro.mov'), main_clip()), sources)
        self.assertEqual(plan['flags'][0]['reason'], 'clips_have_different_frame_rates')
        self.assertTrue(plan['needs_review'])

    def test_refuses_a_recipe_that_leaves_nothing(self):
        with self.assertRaisesRegex(RecipeError, 'nothing to edit'):
            plan_edit(recipe(dict(main_clip(), enabled=False)), SOURCES)


class Assignment(unittest.TestCase):
    silence = dict(type='silence', params=dict(min_pause_seconds=1, keep_pause_seconds=0.5))

    def test_silence_cutting_applies_only_to_the_clips_it_is_assigned_to(self):
        other = dict(id='other', source=dict(type='field', field='other'))
        plan = plan_edit(recipe(other, main_clip(self.silence)), dict(SOURCES, other=MAIN))
        self.assertEqual(plan['clips']['other']['keeps'], [[0.0, 15]])
        self.assertEqual(len(plan['clips']['main']['keeps']), 3)
        self.assertEqual({c['clip'] for c in plan['cuts']}, {'main'})

    def test_it_can_be_assigned_to_several_clips(self):
        other = dict(id='other', source=dict(type='field', field='other'), treatments=[self.silence])
        plan = plan_edit(recipe(other, main_clip(self.silence)), dict(SOURCES, other=MAIN))
        self.assertEqual({c['clip'] for c in plan['cuts']}, {'other', 'main'})

    def test_shared_settings_apply_unless_a_clip_overrides_them(self):
        shared = dict(silence=dict(min_pause_seconds=1, keep_pause_seconds=0))
        other = dict(id='other', source=dict(type='field', field='other'), treatments=[dict(type='silence', params=dict(keep_pause_seconds=1))])
        plan = plan_edit(recipe(other, main_clip(dict(type='silence')), settings=shared), dict(SOURCES, other=MAIN))
        self.assertAlmostEqual(plan['clips']['main']['final_end'] - plan['clips']['main']['final_start'], 9)
        self.assertAlmostEqual(plan['clips']['other']['final_end'] - plan['clips']['other']['final_start'], 11)

    def test_a_treatment_can_be_turned_off_on_one_clip(self):
        plan = plan_edit(recipe(main_clip(dict(self.silence, enabled=False))), SOURCES)
        self.assertEqual(plan['cuts'], [])

    def test_a_clip_without_transcription_cannot_have_silences_cut(self):
        with self.assertRaisesRegex(RecipeError, 'needs a transcription'):
            plan_edit(recipe(file_clip('intro', 'intro.mov', treatments=[self.silence])), SOURCES)


class Trim(unittest.TestCase):
    def test_moves_an_approximate_time_to_the_silence_before_the_speech(self):
        plan = plan_edit(recipe(main_clip(dict(type='trim', params=dict(in_seconds=6.5, precision='approximate')))), SOURCES)
        self.assertAlmostEqual(plan['clips']['main']['keeps'][0][0], 6.9167, places=3)
        self.assertEqual(plan['cuts'][0]['severity'], 'green')
        self.assertIn('merci', plan['cuts'][0]['after'])

    def test_end_keeps_the_last_word_with_a_margin(self):
        plan = plan_edit(recipe(main_clip(dict(type='trim', params=dict(out_seconds=9.5, precision='approximate')))), SOURCES)
        self.assertAlmostEqual(plan['clips']['main']['keeps'][-1][1], 9.0833, places=3)
        self.assertIn('là', plan['cuts'][0]['before'])

    def test_asks_for_a_decision_when_no_silence_is_near(self):
        plan = plan_edit(recipe(main_clip(dict(type='trim', params=dict(in_seconds=8.5, search_seconds=0.2, precision='approximate')))), SOURCES)
        self.assertEqual(plan['cuts'][0]['severity'], 'red')
        self.assertEqual(plan['cuts'][0]['reason'], 'no_silence_near_requested_time')
        self.assertTrue(plan['needs_review'])

    def test_reads_times_from_the_form_and_refuses_a_missing_answer(self):
        trim = dict(type='trim', params=dict(in_field='debut', out_field='fin', precision='approximate'))
        plan = plan_edit(recipe(main_clip(trim)), SOURCES, dict(debut=6.5, fin=9.5))
        self.assertAlmostEqual(plan['final_duration'], 9.0833 - 6.9167, places=3)
        with self.assertRaisesRegex(RecipeError, 'no answer'):
            plan_edit(recipe(main_clip(trim)), SOURCES, dict(debut=1))

    def test_reads_a_passage_from_the_form(self):
        trim = dict(type='trim', params=dict(range_field='passage', precision='approximate'))
        plan = plan_edit(recipe(main_clip(trim)), SOURCES, dict(passage=dict(startSeconds=6.5, endSeconds=9.5)))
        self.assertAlmostEqual(plan['final_duration'], 9.0833 - 6.9167, places=3)
        with self.assertRaisesRegex(RecipeError, 'incomplete'):
            plan_edit(recipe(main_clip(trim)), SOURCES, dict(passage=dict(startSeconds=6.5)))

    def test_refuses_an_end_before_the_start(self):
        with self.assertRaisesRegex(RecipeError, 'before'):
            plan_edit(recipe(main_clip(dict(type='trim', params=dict(in_seconds=9, out_seconds=2)))), SOURCES)

    def test_refuses_removing_a_whole_clip(self):
        with self.assertRaisesRegex(RecipeError, 'whole clip'):
            plan_edit(recipe(main_clip(dict(type='trim', params=dict(in_seconds=15, precision='exact')))), SOURCES)


class Silence(unittest.TestCase):
    def test_shortens_long_pauses_without_touching_speech(self):
        plan = plan_edit(recipe(main_clip(dict(type='silence', params=dict(min_pause_seconds=1, keep_pause_seconds=0.5)))), SOURCES)
        self.assertEqual(len(plan['cuts']), 2)
        for word in WORDS:
            self.assertTrue(any(a <= word['start'] and word['end'] <= b for a, b in plan['clips']['main']['keeps']), word['text'])
        self.assertAlmostEqual(plan['final_duration'], 15 - (3 - 0.5) - (3 - 0.5), places=3)

    def test_keeps_a_pause_shorter_than_the_minimum(self):
        self.assertEqual(plan_edit(recipe(main_clip(dict(type='silence', params=dict(min_pause_seconds=5)))), SOURCES)['cuts'], [])

    def test_cuts_land_on_whole_frames_of_that_clip(self):
        sources = dict(SOURCES, main=dict(MAIN, fps=30))
        plan = plan_edit(recipe(main_clip(dict(type='silence', params=dict(keep_pause_seconds=0.333)))), sources)
        for a, b in plan['clips']['main']['keeps']:
            self.assertAlmostEqual(a * 30, round(a * 30))
            self.assertAlmostEqual(b * 30, round(b * 30))

    def test_flags_a_cut_next_to_doubtful_words(self):
        words = [dict(w, p=0.2) if w['text'] == 'merci' else w for w in WORDS]
        plan = plan_edit(recipe(main_clip(dict(type='silence', params=dict(min_pause_seconds=1)))), dict(SOURCES, main=dict(MAIN, words=words)))
        self.assertEqual(plan['cuts'][0]['severity'], 'orange')


class Placement(unittest.TestCase):
    def test_an_overlay_time_follows_the_cuts_and_earlier_clips(self):
        plan = plan_edit(recipe(file_clip('intro', 'intro.mov'), main_clip(
            dict(type='silence', params=dict(min_pause_seconds=1, keep_pause_seconds=0.5)),
            dict(type='place_clip', params=dict(file='broll.mov', anchor='source_seconds', seconds=7.5)))), SOURCES)
        self.assertAlmostEqual(plan['overlays'][0]['final_start'], 2 + 7.5 - 2.5, places=3)

    def test_inserting_splits_the_clip_and_pushes_the_rest(self):
        plan = plan_edit(recipe(main_clip(dict(type='place_clip', params=dict(file='broll.mov', anchor='source_seconds', seconds=5, mode='insert')))), SOURCES)
        self.assertEqual([i['kind'] for i in plan['sequence']], ['source', 'placed', 'source'])
        self.assertEqual(plan['sequence'][2]['final_start'], 9)
        self.assertEqual(plan['final_duration'], 19)

    def test_a_clip_in_a_removed_passage_is_flagged_and_not_moved(self):
        plan = plan_edit(recipe(main_clip(dict(type='trim', params=dict(in_seconds=6.5)), dict(type='place_clip', params=dict(file='broll.mov', anchor='source_seconds', seconds=1, mode='insert')))), SOURCES)
        self.assertEqual(plan['flags'][0]['reason'], 'clip_time_in_removed_passage')
        self.assertEqual(plan['flags'][0]['severity'], 'red')
        self.assertEqual([i['kind'] for i in plan['sequence']], ['source'])

    def test_refuses_a_placement_without_a_file_or_time(self):
        with self.assertRaisesRegex(RecipeError, 'file is required'):
            plan_edit(recipe(main_clip(dict(type='place_clip', params=dict(seconds=1)))), SOURCES)
        with self.assertRaisesRegex(RecipeError, 'number of seconds is required'):
            plan_edit(recipe(main_clip(dict(type='place_clip', params=dict(file='broll.mov')))), SOURCES)


class Precision(unittest.TestCase):
    def trim(self, **params):
        return main_clip(dict(type='trim', params=params))

    def test_exact_times_are_cut_to_the_frame_without_any_check(self):
        plan = plan_edit(recipe(self.trim(in_seconds=6.51, precision='exact')), dict(SOURCES, main=dict(duration=15, fps=24)))
        self.assertEqual(plan['clips']['main']['keeps'], [[6.5, 15]])
        self.assertEqual((plan['cuts'][0]['severity'], plan['cuts'][0]['reason']), ('green', 'exact_time'))
        self.assertFalse(plan['needs_review'])

    def test_exact_is_the_default_and_approximate_looks_around_the_given_time(self):
        exact = plan_edit(recipe(self.trim(in_seconds=6.5)), SOURCES)
        approximate = plan_edit(recipe(self.trim(in_seconds=6.5, precision='approximate')), SOURCES)
        self.assertAlmostEqual(approximate['clips']['main']['keeps'][0][0], 6.9167, places=3)
        self.assertEqual(exact['clips']['main']['keeps'][0][0], 6.5)

    def test_approximate_needs_a_transcription_but_exact_does_not(self):
        bare = dict(SOURCES, main=dict(duration=15, fps=24))
        with self.assertRaisesRegex(RecipeError, 'needs a transcription'):
            plan_edit(recipe(self.trim(in_seconds=5, precision='approximate')), bare)
        self.assertEqual(plan_edit(recipe(self.trim(in_seconds=5)), bare)['clips']['main']['keeps'], [[5.0, 15]])

    def test_a_form_can_ask_for_the_best_moment_on_every_clip(self):
        other = dict(id='other', source=dict(type='field', field='other'), treatments=[dict(type='trim', params=dict(in_seconds=6.5))])
        plan = plan_edit(recipe(other, self.trim(out_seconds=9.5), settings=dict(trim=dict(precision='approximate'))), dict(SOURCES, other=MAIN))
        self.assertAlmostEqual(plan['clips']['other']['keeps'][0][0], 6.9167, places=3)
        self.assertAlmostEqual(plan['clips']['main']['keeps'][-1][1], 9.0833, places=3)
        self.assertFalse(plan['needs_review'])

    def test_a_time_outside_the_clip_is_flagged_and_ignored(self):
        plan = plan_edit(recipe(self.trim(out_seconds=99, precision='exact')), SOURCES)
        self.assertEqual((plan['flags'][0]['reason'], plan['flags'][0]['severity']), ('time_outside_clip', 'red'))
        self.assertEqual(plan['clips']['main']['keeps'], [[0.0, 15]])
        self.assertTrue(plan['needs_review'])

    def test_refuses_an_unknown_precision_and_a_non_numeric_time(self):
        with self.assertRaisesRegex(RecipeError, 'must be one of'):
            plan_edit(recipe(self.trim(in_seconds=5, precision='maybe')), SOURCES)
        with self.assertRaisesRegex(RecipeError, 'not a time'):
            plan_edit(recipe(self.trim(in_field='debut', precision='exact')), SOURCES, dict(debut='bientôt'))


class Anchors(unittest.TestCase):
    cuts = dict(type='silence', params=dict(min_pause_seconds=1, keep_pause_seconds=0.5))

    def place(self, *extra, **params):
        return main_clip(*extra, dict(type='place_clip', params=dict(file='broll.mov', **params)))

    def test_seconds_after_the_start_are_measured_after_the_cuts(self):
        plain = plan_edit(recipe(self.place(anchor='from_start', seconds=5)), SOURCES)
        self.assertEqual((plain['overlays'][0]['source_time'], plain['overlays'][0]['final_start']), (5, 5))
        cut = plan_edit(recipe(self.place(self.cuts, anchor='from_start', seconds=5)), SOURCES)
        self.assertEqual((cut['overlays'][0]['source_time'], cut['overlays'][0]['final_start']), (7.5, 5))

    def test_a_percentage_of_the_clip_is_measured_after_the_cuts_and_earlier_clips(self):
        plan = plan_edit(recipe(file_clip('intro', 'intro.mov'), self.place(self.cuts, anchor='percent', percent=50)), SOURCES)
        self.assertEqual(plan['overlays'][0]['source_time'], 7.5)
        self.assertEqual(plan['overlays'][0]['final_start'], 2 + 5)

    def test_one_hundred_percent_is_past_the_end(self):
        plan = plan_edit(recipe(self.place(anchor='percent', percent=100)), SOURCES)
        self.assertEqual(plan['flags'][0]['reason'], 'anchor_outside_clip')
        self.assertEqual(plan['overlays'], [])

    def test_before_the_end_starts_that_many_seconds_before_the_clip_ends(self):
        plan = plan_edit(recipe(self.place(self.cuts, anchor='before_end', seconds=3)), SOURCES)
        self.assertEqual((plan['overlays'][0]['source_time'], plan['overlays'][0]['final_start']), (12.0, 7))
        self.assertEqual([(f['reason'], f['severity']) for f in plan['flags']], [('overlay_extends_past_clip', 'orange')])

    def test_a_moment_beyond_the_clip_is_flagged(self):
        plan = plan_edit(recipe(self.place(anchor='from_start', seconds=99)), SOURCES)
        self.assertEqual(plan['flags'][0]['reason'], 'anchor_outside_clip')

    def test_a_spoken_phrase_finds_its_moment_and_follows_the_cuts(self):
        plan = plan_edit(recipe(self.place(self.cuts, anchor='phrase', phrase="Merci, d'être")), SOURCES)
        self.assertEqual((plan['overlays'][0]['source_time'], plan['overlays'][0]['final_start']), (7.0, 4.5))

    def test_a_phrase_that_is_not_found_asks_for_a_decision(self):
        plan = plan_edit(recipe(self.place(anchor='phrase', phrase='bonsoir')), SOURCES)
        self.assertEqual((plan['flags'][0]['reason'], plan['flags'][0]['severity']), ('phrase_not_found', 'red'))
        self.assertTrue(plan['needs_review'])
        self.assertEqual(plan['overlays'], [])

    def test_a_phrase_needs_a_transcription(self):
        with self.assertRaisesRegex(RecipeError, 'needs a transcription'):
            plan_edit(recipe(file_clip('intro', 'intro.mov', treatments=[dict(type='place_clip', params=dict(file='broll.mov', anchor='phrase', phrase='a'))])), SOURCES)

    def test_a_time_from_the_form_refers_to_the_clips_own_video(self):
        plan = plan_edit(recipe(self.place(anchor='field', field='quand')), SOURCES, dict(quand=6))
        self.assertEqual(plan['overlays'][0]['final_start'], 6)
        with self.assertRaisesRegex(RecipeError, 'no answer'):
            plan_edit(recipe(self.place(anchor='field', field='quand')), SOURCES)
        with self.assertRaisesRegex(RecipeError, 'not a time'):
            plan_edit(recipe(self.place(anchor='field', field='quand')), SOURCES, dict(quand='bientôt'))

    def test_each_moment_requires_its_own_value(self):
        for params, pattern in [(dict(anchor='percent'), 'percentage'), (dict(anchor='from_start'), 'seconds'),
                                (dict(anchor='field'), 'form question'), (dict(anchor='phrase'), 'word or phrase')]:
            with self.assertRaisesRegex(RecipeError, pattern):
                plan_edit(recipe(self.place(**params)), SOURCES)

    def test_refuses_an_unknown_moment(self):
        with self.assertRaisesRegex(RecipeError, 'must be one of'):
            plan_edit(recipe(self.place(anchor='middle')), SOURCES)

    def test_two_overlays_playing_together_keep_only_the_first(self):
        clip = main_clip(dict(type='place_clip', params=dict(file='broll.mov', anchor='from_start', seconds=5)),
                         dict(type='place_clip', params=dict(file='fin.mov', anchor='from_start', seconds=6)))
        plan = plan_edit(recipe(clip), SOURCES)
        self.assertEqual([o['file'] for o in plan['overlays']], ['broll.mov'])
        self.assertEqual((plan['flags'][0]['reason'], plan['flags'][0]['detail']), ('overlay_overlap', 'fin.mov'))

    def test_an_overlay_adds_a_marker_and_can_add_a_chapter(self):
        plan = plan_edit(recipe(self.place(anchor='from_start', seconds=5, chapter_title='Abonnez-vous')), SOURCES)
        self.assertEqual(plan['chapters'], [dict(time=5.0, title='Abonnez-vous')])
        self.assertEqual([(m['kind'], m['color']) for m in plan['markers']], [('overlay', 'blue')])
        self.assertFalse(plan['needs_review'])

    def test_an_inserted_clip_can_start_a_chapter(self):
        plan = plan_edit(recipe(self.place(anchor='source_seconds', seconds=5, mode='insert', chapter_title='Pause')), SOURCES)
        self.assertEqual(plan['chapters'], [dict(time=5.0, title='Pause')])


class Markers(unittest.TestCase):
    def test_markers_use_the_final_time_after_earlier_clips(self):
        plan = plan_edit(recipe(file_clip('intro', 'intro.mov'), main_clip(dict(type='silence', params=dict(min_pause_seconds=1, keep_pause_seconds=0.5)))), SOURCES)
        self.assertGreater(plan['markers'][0]['time'], 2)
        self.assertEqual(plan['markers'][0]['clip'], 'main')
        self.assertIn('|', plan['markers'][0]['note'])

    def test_a_trimmed_start_is_marked_where_the_clip_begins(self):
        plan = plan_edit(recipe(file_clip('intro', 'intro.mov'), main_clip(dict(type='trim', params=dict(in_seconds=6.5)))), SOURCES)
        self.assertAlmostEqual(plan['markers'][0]['time'], 2)


class Finishing(unittest.TestCase):
    def test_finishing_steps_apply_to_the_whole_edit(self):
        plan = plan_edit(recipe(main_clip(), finish=[dict(type='normalize'), dict(type='fade', params=dict(video_seconds=0.5))]), SOURCES)
        self.assertEqual(plan['audio'], [dict(operation='normalize', target_lufs=-16)])
        self.assertEqual(plan['fades'], [dict(video_seconds=0.5, audio_seconds=0.03)])

    def test_an_instruction_always_requires_review(self):
        plan = plan_edit(recipe(main_clip(), finish=[dict(type='instruction', params=dict(body='Garder le rire'))]), SOURCES)
        self.assertTrue(plan['needs_review'])
        self.assertEqual(plan['flags'][0]['detail'], 'Garder le rire')

    def test_a_clean_plan_needs_no_review(self):
        self.assertFalse(plan_edit(recipe(main_clip(dict(type='silence', params=dict(min_pause_seconds=1)))), SOURCES)['needs_review'])


class Transcripts(unittest.TestCase):
    def test_reads_resolve_words_relative_to_the_media_start(self):
        words = words_from_resolve(dict(segments=[dict(words=[
            dict(start='01:00:02:00', end='01:00:02:12', text='bonjour'), dict(start='01:00:02:12', end='01:00:04:00', text='(...)'),
            dict(start='01:00:04:00', end='01:00:04:12', text='merci')])]), 24, '01:00:00:00')
        self.assertEqual([w['text'] for w in words], ['bonjour', 'merci'])
        self.assertEqual(words[0]['start'], 2.0)
        self.assertEqual(words[1]['end'], 4.5)

    def test_reads_whisper_words(self):
        self.assertEqual(words_from_whisper(dict(segments=[dict(words=[dict(start=1, end=2, text='a', p=0.9)])])), [dict(start=1, end=2, text='a', p=0.9)])

    def test_refuses_an_invalid_timecode(self):
        with self.assertRaises(ValueError):
            timecode_seconds('nope', 24)


class Comparison(unittest.TestCase):
    def test_measures_the_distance_to_the_editors_cuts(self):
        result = compare_cuts([[1.0, 5.1]], [[1.0, 5.0]], fps=24)
        self.assertEqual(result['matched'], 2)
        self.assertEqual(result['within_one_frame'], 1)
        self.assertAlmostEqual(result['p90_ms'], 100, places=0)

    def test_reports_missed_and_extra_cuts(self):
        result = compare_cuts([[0, 3], [20, 30]], [[0, 10], [20, 30]])
        self.assertEqual(result['missed'], [10])
        self.assertEqual(result['extra'], [3])

    def test_finds_a_cut_inside_a_word(self):
        result = compare_cuts([[2.2, 15]], [[2.0, 15]], WORDS)
        self.assertEqual(result['cuts_inside_words'], [dict(time=2.2, word='bonjour')])

    def test_converts_a_timeline_to_source_passages(self):
        clips = [dict(start=100, source_start_frame=240, source_end_frame=480), dict(start=0, source_start_frame=0, source_end_frame=100)]
        self.assertEqual(reference_keeps(clips, 24), [[0, 100 / 24], [10, 20]])


if __name__ == '__main__':
    unittest.main()

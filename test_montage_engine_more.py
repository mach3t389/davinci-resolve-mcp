import unittest

from montage_engine import broll, cameras, panels
from montage_engine.model import Clip, Snapshot
from montage_engine.series import SERIES, VIDEO_TYPES, resolve_directives, roles_of


def clip(kind, track, tname, start, end, name='x.mov', file='f.mov'):
    return Clip(kind=kind, track=track, track_name=tname, start=start, end=end, file=file, name=name)


def snap(clips):
    s = Snapshot(clips=clips, out_frame=5000)
    for c in clips:
        s.track_names[(c.kind, c.track)] = c.track_name
    return s


class PanelsTest(unittest.TestCase):
    def test_alpha_profile(self):
        # opaque from frame 0, fade-out over the last 18 of 100 frames (the Lili panels)
        vals = [255.0] * 82 + [255 * (1 - i / 18) for i in range(1, 19)]
        p = panels.profile_from_alpha(vals)
        self.assertEqual((p['n'], p['in_full'], p['out_start']), (100, 0, 82))
        self.assertIsNone(panels.profile_from_alpha([0, 0, 0]))
        self.assertIsNone(panels.profile_from_alpha([]))

    def test_kind_comes_from_what_is_under_the_panel(self):
        voice = clip('audio', 1, 'Audio 1', 0, 1000)
        pause = clip('video', 4, 'Motion', 1100, 1300)
        quote = clip('video', 4, 'Motion', 200, 400)
        s = snap([voice, pause, quote])
        self.assertEqual(panels.panel_kind(s, pause), 'chapter')
        self.assertEqual(panels.panel_kind(s, quote), 'overlay')

    def test_chain_second_panel_is_visible_when_the_first_fades(self):
        pa = dict(in_full=0, out_start=82)
        pb = dict(in_full=0, out_start=82)
        # first at 1000..1100; second planned at 1110 (10 frames after the end): move it earlier
        self.assertEqual(panels.chain_start(1000, pa, pb, 12), 1000 + 82 - 12)
        a, b = clip('video', 4, 'Motion', 1000, 1100, 'Lili01_v001.mov'), clip('video', 4, 'Motion', 1110, 1210, 'Lili02_v003.mov')
        plans = panels.plan_chains([(a, 'chapter'), (b, 'chapter')],
                                   {'Lili01': pa, 'Lili02': pb})
        self.assertEqual(plans[0]['new_start'], 1070)
        self.assertEqual(plans[0]['shift'], 1070 - 1110)
        self.assertTrue(plans[0]['track_above'])

    def test_far_panels_overlay_panels_and_missing_alpha_are_not_chained(self):
        pa = dict(in_full=0, out_start=82)
        a, far = clip('video', 4, 'Motion', 1000, 1100, 'Lili01_v001.mov'), clip('video', 4, 'Motion', 1200, 1300, 'Lili02_v001.mov')
        self.assertEqual(panels.plan_chains([(a, 'chapter'), (far, 'chapter')], {'Lili01': pa, 'Lili02': pa}), [])
        near = clip('video', 4, 'Motion', 1110, 1210, 'Visuels01_v001.mov')
        self.assertEqual(panels.plan_chains([(a, 'chapter'), (near, 'overlay')], {'Lili01': pa}), [])
        unknown = clip('video', 4, 'Motion', 1110, 1210, 'Autre_v001.mov')
        self.assertIn('error', panels.plan_chains([(a, 'chapter'), (unknown, 'chapter')], {'Lili01': pa})[0])

    def test_a_chained_panel_never_starts_before_the_first(self):
        a, b = clip('video', 4, 'Motion', 1000, 1030, 'A_v001.mov'), clip('video', 4, 'Motion', 1031, 1100, 'B_v001.mov')
        plans = panels.plan_chains([(a, 'chapter'), (b, 'chapter')], {'A': dict(in_full=0, out_start=5), 'B': dict(in_full=40, out_start=60)})
        self.assertEqual(plans[0]['new_start'], 1000)


class CamerasTest(unittest.TestCase):
    def values(self, **cam):
        v = resolve_directives(type_=VIDEO_TYPES['interview'], series=SERIES['masterclass'])[0]
        v['cameras'].update(cam)
        return v

    def test_alternates_within_the_series_limits(self):
        fps = 24
        cands = list(range(120, 24 * 120, 72))              # a cut every 3 s for 2 minutes
        plan = cameras.plan_runs(0, 24 * 120, cands, set(), [], self.values(), fps)
        runs = plan['runs']
        self.assertEqual(runs[0][2], 'main')                 # starts on the main camera
        for a, b, cam in runs[:-1]:
            n = (b - a) / fps
            self.assertGreaterEqual(n, 6 if cam == 'main' else 3)
            if cam == 'secondary':
                self.assertLessEqual(n, 10 + 3)              # ends by the next candidate after the maximum
        self.assertTrue(0.55 <= plan['share_main'] <= 0.9)
        self.assertEqual([r[1] for r in runs[:-1]], sorted(r[1] for r in runs[:-1]))

    def test_never_switches_inside_an_overlay_fade(self):
        cands = [200, 300, 400, 500, 600]
        plan = cameras.plan_runs(0, 800, cands, set(), [(190, 310)], self.values(main_min_seconds=1, secondary_seconds=[1, 5]), 24)
        self.assertTrue(all(not (190 < r[1] < 310) for r in plan['runs'][:-1]))

    def test_a_jump_cut_is_hidden_by_switching_when_allowed(self):
        plan = cameras.plan_runs(0, 2000, [400, 800, 1200], {800}, [], self.values(main_min_seconds=10), 24)
        self.assertIn(800, [r[1] for r in plan['runs']])
        self.assertNotIn(800, plan['visible_jump_cuts'])
        tooshort = cameras.plan_runs(0, 2000, [60, 800], {60}, [], self.values(), 24)
        self.assertIn(60, tooshort['visible_jump_cuts'])      # too early to switch: reported, not hidden

    def test_apply_runs_toggles_the_top_camera(self):
        class C:
            def __init__(self, en): self.en = en
            def GetClipEnabled(self): return self.en
            def SetClipEnabled(self, v): self.en = v
        a, b = C(True), C(True)
        n = cameras.apply_runs([(a, 0), (b, 300)], [(0, 300, 'main'), (300, 600, 'secondary')], main_is_top=True)
        self.assertEqual((a.en, b.en, n), (True, False, 1))


class BrollTest(unittest.TestCase):
    def test_scenes_group_shots_close_in_time_on_one_card(self):
        items = [dict(file='A001_a.mov', time=0, card='A001'), dict(file='A001_b.mov', time=30, card='A001'),
                 dict(file='A001_c.mov', time=500, card='A001'), dict(file='B002_a.mov', time=20, card='B002')]
        scenes = broll.group_scenes(items)
        self.assertEqual(sorted(len(s) for s in scenes), [1, 1, 2])
        self.assertEqual(broll.camera_card('A029_03011557_C017.mov'), 'A029')

    def test_unused_shots_are_parked_not_deleted(self):
        un = broll.unused(['a', 'b', 'c'], ['b'])
        self.assertEqual(un, ['a', 'c'])
        plan = broll.plan_parking(un, 1000, {'a': 100, 'c': 50})
        self.assertEqual(plan, [('a', 1576, 100), ('c', 1676, 50)])
        self.assertEqual(broll.plan_parking(['z'], 1000, {}), [])          # unknown duration: left alone

    def test_roles_include_new_track_names(self):
        s = snap([clip('video', 3, "Plans d'appui", 0, 10), clip('video', 4, 'Motion', 0, 10)])
        self.assertEqual(set(roles_of(s).values()), {'broll', 'graphic'})


if __name__ == '__main__':
    unittest.main()

import unittest

from montage_engine import broll, cameras, panels
from montage_engine.model import Clip, Snapshot
from montage_engine.series import SERIES, VIDEO_TYPES, resolve_directives, roles_of


def clip(kind, track, tname, start, end, name='x.mov', file='f.mov', src=0, enabled=True):
    return Clip(kind=kind, track=track, track_name=tname, start=start, end=end, file=file, name=name,
                src_start=src, enabled=enabled)


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


class FadeInformationTest(unittest.TestCase):
    """Fades are read from the render's sidecar; coverage decides whether a panel constrains the cuts around it."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.dir = Path(tempfile.mkdtemp())
        self.render = str(self.dir / 'Lili01_v001.mov')
        Path(self.render).write_bytes(b'x')

    def test_sidecar_roundtrip_and_it_wins_over_measuring(self):
        panels.write_sidecar(self.render, n=100, in_full=0, out_start=82, coverage='fullscreen')
        prof = panels.profile_for(self.render, measure=False)
        self.assertEqual((prof['n'], prof['out_start'], prof['coverage'], prof['source']), (100, 82, 'fullscreen', 'sidecar'))
        calls = []
        orig = panels.alpha_profile_file
        panels.alpha_profile_file = lambda p: calls.append(p) or None          # must not run: the sidecar answers
        try:
            self.assertEqual(panels.profile_for(self.render)['source'], 'sidecar')
        finally:
            panels.alpha_profile_file = orig
        self.assertEqual(calls, [])

    def test_a_broken_sidecar_is_ignored_not_half_trusted(self):
        import json
        from pathlib import Path
        path = panels.sidecar_path(self.render)
        for bad in ({'n': 100}, {'n': 100, 'in_full': 5, 'out_start': 3, 'coverage': 'fullscreen'},
                    {'n': 100, 'in_full': 0, 'out_start': 82, 'coverage': 'half'},
                    {'n': '100', 'in_full': 0, 'out_start': 82, 'coverage': 'partial'}, [1, 2]):
            Path(path).write_text(json.dumps(bad), encoding='utf8')
            self.assertIsNone(panels.read_sidecar(self.render), bad)
        Path(path).write_text('{not json', encoding='utf8')
        self.assertIsNone(panels.read_sidecar(self.render))
        self.assertIsNone(panels.profile_for(self.render, measure=False))
        with self.assertRaises(ValueError):
            panels.write_sidecar(self.render, 10, 8, 4, 'fullscreen')

    def test_coverage_is_measured_from_the_peak_alpha_when_there_is_no_sidecar(self):
        self.assertEqual(panels.profile_from_alpha([255.0] * 50)['coverage'], 'fullscreen')
        self.assertEqual(panels.profile_from_alpha([60.0] * 50)['coverage'], 'partial')      # a lower third
        self.assertEqual(panels.profile_from_alpha([1.0] * 5)['coverage'], 'fullscreen')     # 0..1 scale

    def test_a_10_bit_render_is_measured_on_the_right_scale(self):
        """Remotion renders yuva444p10le: without format=gray an opaque frame reads 3760 and everything looks full screen."""
        import shutil
        import subprocess
        if not shutil.which('ffmpeg'):
            self.skipTest('ffmpeg is not installed')
        out = {}
        for name, alpha in (('full', 'if(lt(N,2),0,if(lt(N,14),(N-2)*1023/12,if(lt(N,80),1023,if(lt(N,96),(95-N)*1023/16,0))))'),
                            ('partial', 'if(lt(X,100),1023,0)')):
            path = str(self.dir / f'{name}.mov')
            r = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                                f"color=c=white:s=320x180:r=24:d=4,format=yuva444p10le,geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='{alpha}'",
                                '-c:v', 'prores_ks', '-profile:v', '4444', path], capture_output=True)
            if r.returncode != 0:
                self.skipTest('this ffmpeg cannot write ProRes 4444 with alpha')
            out[name] = panels.alpha_profile_file(path)
        self.assertEqual((out['full']['in_full'], out['full']['out_start'], out['full']['coverage']), (14, 80, 'fullscreen'))
        self.assertEqual(out['partial']['coverage'], 'partial')

    def scene(self):
        from montage_engine import checks
        cams = [clip('video', 1, 'Camera 2 (slider)', 0, 600, file='c2.mov'),
                clip('video', 2, 'Camera 1 (principale)', 0, 200, file='c1.mov'),
                clip('video', 2, 'Camera 1 (principale)', 200, 600, file='c1.mov', src=900, enabled=False)]
        overlay = clip('video', 4, 'Motion', 195, 300, file='over.mov', name='Visuels01_v001.mov')
        s = snap(cams + [overlay])
        s.out_frame = 600
        return checks, s

    def test_a_partial_overlay_over_several_shots_lets_the_camera_change_under_it(self):
        checks, s = self.scene()
        v = resolve_directives()[0]
        self.assertEqual(len(checks.check_cut_in_fade_zone(s, v)), 1)                       # unknown render: conservative
        partial = {'over.mov': dict(n=105, in_full=0, out_start=90, coverage='partial')}
        self.assertEqual(checks.check_cut_in_fade_zone(s, v, profiles=partial), [])
        full = {'over.mov': dict(n=105, in_full=0, out_start=90, coverage='fullscreen')}
        self.assertEqual(len(checks.check_cut_in_fade_zone(s, v, profiles=full)), 1)        # full screen: still constrained

    def test_the_fades_of_the_render_replace_the_directive_estimate(self):
        checks, s = self.scene()
        v = resolve_directives()[0]
        # the directive says 12 in / 18 out; this render fades out over 40 frames (n=105, out_start=65)
        cover, fades = checks.cover_and_fades(s, v, {'over.mov': dict(n=105, in_full=0, out_start=65, coverage='fullscreen')})
        self.assertIn((300 - 40, 300 + 12), fades)
        self.assertEqual(cover, [(195 + 12, 300 - 40)])

    def test_profiles_are_collected_from_sidecars_for_graphic_clips_only(self):
        panels.write_sidecar(self.render, 100, 0, 82, 'fullscreen')
        s = snap([clip('video', 4, 'Motion', 0, 100, file=self.render), clip('video', 2, 'Camera 1 (principale)', 0, 100, file='c.mov')])
        from montage_engine.series import role_of
        got = panels.profiles_for_snapshot(s, role_of)
        self.assertEqual(list(got), [self.render])

    def test_chains_use_the_profile_of_the_file(self):
        pa = dict(in_full=0, out_start=82, n=100, coverage='fullscreen')
        a, b = clip('video', 4, 'Motion', 1000, 1100, 'Lili01_v001.mov', file='/m/a.mov'), clip('video', 4, 'Motion', 1110, 1210, 'Lili02_v003.mov', file='/m/b.mov')
        plans = panels.plan_chains([(a, 'chapter'), (b, 'chapter')], {'/m/a.mov': pa, '/m/b.mov': pa})
        self.assertEqual(plans[0]['new_start'], 1070)


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

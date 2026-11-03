import unittest
from camera_plan import make_plan,runs_from_clips

class CameraPlanTests(unittest.TestCase):
    def clip(self,a,z,source_start,source_end,name='camera.braw'):
        return dict(start=a,end=z,source_start_frame=source_start,source_end_frame=source_end,name=name)

    def test_continuous_edits_merge_but_removed_footage_forces_angle_change(self):
        clips=[self.clip(0,480,0,600),self.clip(480,960,600,1200),
               self.clip(960,3360,1500,4500)]
        runs=runs_from_clips(clips,0,3360)
        self.assertEqual(runs,[dict(start=0,end=960),dict(start=960,end=3360)])
        plan=make_plan(clips,0,3360)
        left=next(s for s in plan['shots'] if s['end']==960)
        right=next(s for s in plan['shots'] if s['start']==960)
        self.assertNotEqual(left['camera'],right['camera'])

    def test_schedule_preserves_coverage_and_gaps_and_prefers_wide(self):
        clips=[self.clip(i*5000,i*5000+4800,i*8000,i*8000+6000) for i in range(12)]
        plan=make_plan(clips,0,60000)
        self.assertAlmostEqual(plan['percentages']['1'],200/3,delta=0.15)
        self.assertGreater(plan['percentages']['3'],plan['percentages']['2'])
        self.assertEqual(sum(s['end']-s['start'] for s in plan['shots']),12*4800)
        for run in plan['runs']:
            shots=[s for s in plan['shots'] if run['start']<=s['start']<run['end']]
            self.assertEqual(shots[0]['start'],run['start'])
            self.assertEqual(shots[-1]['end'],run['end'])
            self.assertTrue(all(a['end']==b['start'] for a,b in zip(shots,shots[1:])))
        self.assertFalse(plan['short_shots'])

    def test_short_existing_section_is_reported(self):
        clips=[self.clip(0,2400,0,3000),self.clip(2400,2507,4000,4134),self.clip(2507,4907,5000,8000)]
        plan=make_plan(clips,0,4907)
        self.assertTrue(any(s['start']==2400 and s['end']==2507 for s in plan['short_shots']))

    def test_shorter_revision_has_10_to_15_second_secondary_shots(self):
        clips=[self.clip(i*5000,i*5000+4800,i*8000,i*8000+6000) for i in range(12)]
        plan=make_plan(clips,0,60000,rhythm='10-15')
        old=make_plan(clips,0,60000)
        self.assertGreater(len(plan['shots']),len(old['shots']))
        self.assertAlmostEqual(plan['percentages']['1'],200/3,delta=0.15)
        for shot in plan['shots']:
            duration=(shot['end']-shot['start'])/24
            if shot['camera']!=1:self.assertTrue(10<=duration<=15)
            else:self.assertLessEqual(duration,35)

if __name__=='__main__':unittest.main()

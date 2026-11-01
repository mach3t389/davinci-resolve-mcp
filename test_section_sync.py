import unittest
from sync_bridge import SectionSync


class SectionTimecode(unittest.TestCase):
    def test_ntsc_reference_tc(self):
        self.assertEqual(SectionSync.tc_seconds('19:16:57:01'),19*3600+16*60+57+1/30)
        self.assertAlmostEqual(SectionSync.tc_seconds('19:48:24:18')-SectionSync.tc_seconds('19:16:57:01'),1887+17/30)

    def test_invalid_timecode(self):
        for value in ('','19:78:00:00','19:00:60:00','19:00:00:30','19:00:00;xx'):
            with self.assertRaises(ValueError): SectionSync.tc_seconds(value)


if __name__=='__main__': unittest.main()

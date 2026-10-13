import unittest
from sync_bridge import SyncBridge, assess_pair


class SyncGuards(unittest.TestCase):
    def test_audio_only_move_rejected(self):
        report=assess_pair([(100,200),(100,200)],[(100,200),(80,180)],[(0,99),(0,99)],[(0,99),(0,99)])
        self.assertFalse(report['paired_movement_matches'])

    def test_paired_trim_reported(self):
        report=assess_pair([(100,200),(100,200)],[(80,160),(80,160)],[(0,99),(0,99)],[(20,99),(20,99)])
        self.assertTrue(report['paired_movement_matches'])
        self.assertFalse(report['target_duration_preserved'])
        self.assertEqual(report['source_boundary_changes'][0]['left_frames'],20)

    def test_same_positions_but_wrong_source_rejected(self):
        report=assess_pair([(100,200),(100,200)],[(80,180),(80,180)],[(0,99),(0,99)],[(0,99),(1,100)])
        self.assertFalse(report['paired_movement_matches'])

    def bridge(self):
        def forbidden():
            self.fail('Rejected requests must not access Resolve.')
        return SyncBridge(forbidden, None, None, None)

    def test_invalid_sync_mode(self):
        with self.assertRaises(ValueError):
            self.bridge().preview('timeline', 'audio', 'media', 'guess')

    def test_unknown_plan_does_not_write(self):
        with self.assertRaises(ValueError): self.bridge().create('unknown')

    def test_attempts_cannot_be_replayed(self):
        for state in ('attempted', 'completed'):
            bridge = self.bridge()
            bridge.plans['token'] = {'state': state}
            with self.assertRaises(ValueError): bridge.create('token')

    def test_partial_copy_reported_without_retry(self):
        bridge = self.bridge()
        bridge.plans['token'] = {'state': 'attempted', 'copy_id': 'partial-copy', 'error': 'test error'}
        report = bridge.status('token')
        self.assertEqual(report['copy_id'], 'partial-copy')
        self.assertFalse(report['retry_allowed'])
        self.assertEqual(report['error'], 'test error')


if __name__ == '__main__': unittest.main()

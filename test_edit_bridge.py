import unittest
from edit_bridge import EditBridge


class Guards(unittest.TestCase):
    def bridge(self):
        def forbidden():
            self.fail('Resolve must not be accessed for a rejected plan')
        return EditBridge(forbidden, None, None)

    def test_unknown_plan(self):
        with self.assertRaises(ValueError): self.bridge().create('unknown', '')

    def test_gap_requires_exact_confirmation(self):
        b=self.bridge()
        b.plans['token']={'state':'previewed','mode':'keep_with_gaps'}
        for answer in ('','yes','CONFIRM another'):
            with self.assertRaises(ValueError): b.create('token',answer)
        self.assertEqual(b.plans['token']['state'],'previewed')

    def test_attempted_import_never_replayed(self):
        b=self.bridge()
        for state in ('attempted','created','verification_failed'):
            b.plans['token']={'state':state}
            with self.assertRaises(ValueError): b.create('token','CONFIRM token')

if __name__=='__main__': unittest.main()

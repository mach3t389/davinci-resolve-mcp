import tempfile
import unittest
from pathlib import Path

import motion_bridge
import stock_bridge

ROOT = Path(tempfile.mkdtemp()) / 'Tournages' / 'Projet X'
(ROOT / 'Motion').mkdir(parents=True)
(ROOT / 'Motion' / motion_bridge.MARKER).write_text('')
VIDEOS = ROOT / 'Perso' / 'Assets' / 'Videos'
VIDEOS.mkdir(parents=True)

def clip(folder, name):
    path = folder / name
    path.write_bytes(b'x')
    return path

class StockPathTest(unittest.TestCase):
    def test_accepts_video_in_assets_of_a_motion_project(self):
        self.assertEqual(stock_bridge.project_name(clip(VIDEOS, 'a.mp4')), 'Projet X')

    def test_rejects_other_extensions_and_folders(self):
        with self.assertRaises(ValueError):
            stock_bridge.project_name(clip(VIDEOS, 'a.exe'))
        with self.assertRaises(ValueError):
            stock_bridge.project_name(clip(ROOT / 'Perso', 'b.mp4'))  # not in Assets
        outside = Path(tempfile.mkdtemp()) / 'Assets'
        outside.mkdir()
        with self.assertRaises(ValueError):
            stock_bridge.project_name(clip(outside, 'c.mp4'))  # no Motion project beside it

if __name__ == '__main__':
    unittest.main()

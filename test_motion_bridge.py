import os
import tempfile
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ['MOTION_OUT_DIR'] = str(TMP)
import motion_bridge  # noqa: E402

class Item:
    def __init__(self, path):
        self.path = str(path)
    def GetMediaId(self): return 'm1'
    def GetName(self): return Path(self.path).name
    def GetClipProperty(self, key): return self.path if key == 'File Path' else '30'
    def ReplaceClip(self, path):
        self.path = path
        return True

class Folder:
    def __init__(self, name, clips=()):
        self.name, self.clips, self.subs = name, list(clips), []
    def GetName(self): return self.name
    def GetClipList(self): return self.clips
    def GetSubFolderList(self): return self.subs

class Pool:
    def __init__(self):
        self.root = Folder('Master')
        self.current = self.root
    def GetRootFolder(self): return self.root
    def AddSubFolder(self, parent, name):
        parent.subs.append(Folder(name))
        return parent.subs[-1]
    def GetCurrentFolder(self): return self.current
    def SetCurrentFolder(self, f): self.current = f
    def ImportMedia(self, paths):
        item = Item(paths[0])
        self.current.clips.append(item)
        return [item]

class Project:
    def __init__(self): self.pool = Pool()
    def GetMediaPool(self): return self.pool

def render(name):
    path = TMP / name
    path.write_bytes(b'x')
    return path

class MotionBridgeTest(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.bridge = motion_bridge.MotionBridge(lambda: (None, self.p))

    def test_import_then_replace_updates_path_and_reports_previous(self):
        v1, v2 = render('Titre_v001.mov'), render('Titre_v002.mov')
        self.assertEqual(self.bridge.import_clip(v1)['media_id'], 'm1')
        self.assertIs(self.p.pool.current, self.p.pool.root)  # selection restored
        result = self.bridge.replace_clip('m1', v2)
        self.assertTrue(result['verified'])
        self.assertEqual(Path(result['previous_file']), v1.resolve())

    def test_rejects_files_outside_render_folder(self):
        outside = Path(tempfile.mkdtemp()) / 'x.mov'
        outside.write_bytes(b'x')
        with self.assertRaises(ValueError):
            self.bridge.import_clip(outside)

    def test_rejects_duplicate_import(self):
        v1 = render('Dup_v001.mov')
        self.bridge.import_clip(v1)
        with self.assertRaises(ValueError):
            self.bridge.import_clip(v1)

if __name__ == '__main__':
    unittest.main()

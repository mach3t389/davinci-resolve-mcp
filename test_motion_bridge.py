import tempfile
import unittest
from pathlib import Path

import motion_bridge

PROJECT = Path(tempfile.mkdtemp())
(PROJECT / motion_bridge.MARKER).write_text('')
TMP = PROJECT / 'out'
TMP.mkdir()

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
        archived = TMP / motion_bridge.ARCHIVE / 'Titre_v001.mov'
        self.assertTrue(result['archived'])
        self.assertEqual(Path(result['previous_file']), archived.resolve())
        self.assertFalse(v1.exists())  # out/ keeps only the version Resolve uses

    def test_undo_brings_archived_version_back_to_out(self):
        v1, v2 = render('Undo_v001.mov'), render('Undo_v002.mov')
        self.bridge.import_clip(v1)
        first = self.bridge.replace_clip('m1', v2)
        undo = self.bridge.replace_clip('m1', first['previous_file'])
        self.assertTrue(undo['verified'])
        self.assertEqual(Path(undo['file']), v1.resolve())
        self.assertTrue(v1.exists())
        self.assertTrue((TMP / motion_bridge.ARCHIVE / 'Undo_v002.mov').exists())

    def test_rejects_files_outside_render_folder(self):
        outside = Path(tempfile.mkdtemp()) / 'x.mov'
        outside.write_bytes(b'x')
        unmarked = Path(tempfile.mkdtemp()) / 'out'
        unmarked.mkdir()
        (unmarked / 'y.mov').write_bytes(b'x')
        nested = PROJECT / 'design'
        nested.mkdir(exist_ok=True)
        (nested / 'z.mov').write_bytes(b'x')
        for path in (outside, unmarked / 'y.mov', nested / 'z.mov'):
            with self.assertRaises(ValueError):
                self.bridge.import_clip(path)

    def test_rejects_duplicate_import(self):
        v1 = render('Dup_v001.mov')
        self.bridge.import_clip(v1)
        with self.assertRaises(ValueError):
            self.bridge.import_clip(v1)

class TItem:
    def __init__(self, name, start, end): self.name, self.start, self.end = name, start, end
    def GetName(self): return self.name
    def GetStart(self): return self.start
    def GetEnd(self): return self.end

class Timeline:
    def __init__(self, uid, tracks, markers):
        self.uid, self.tracks, self.markers, self.names = uid, tracks, markers, {}
    def GetUniqueId(self): return self.uid
    def GetName(self): return self.uid
    def GetStartFrame(self): return 1000
    def GetEndFrame(self): return 1000 + 500
    def GetMarkers(self): return dict(self.markers)
    def GetTrackCount(self, kind): return len([k for k in self.tracks if k[0] == kind])
    def GetItemListInTrack(self, kind, i): return self.tracks[(kind, i)]
    def AddTrack(self, kind):
        self.tracks[(kind, self.GetTrackCount(kind) + 1)] = []
        return True
    def SetTrackName(self, kind, i, name): self.names[(kind, i)] = name
    def DuplicateTimeline(self, name):
        return Timeline(name, {k: list(v) for k, v in self.tracks.items()}, self.markers)

class EditProject(Project):
    def __init__(self, timeline):
        super().__init__()
        self.current = timeline
        self.pool.AppendToTimeline = self.append
    def GetCurrentTimeline(self): return self.current
    def SetCurrentTimeline(self, t):
        self.current = t
        return True
    def append(self, infos):
        made = []
        for info in infos:
            item = TItem(info['mediaPoolItem'].GetName(), info['recordFrame'], info['recordFrame'] + 30)
            self.current.tracks[('video', info['trackIndex'])].append(item)
            made.append(item)
        return made

class PlaceOnCopyTest(unittest.TestCase):
    def setUp(self):
        self.source = Timeline('src', {('video', 1): [TItem('a.braw', 1000, 1400)], ('audio', 1): [TItem('a.braw', 1000, 1400)]}, {10: {}, 90: {}})
        self.p = EditProject(self.source)
        self.bridge = motion_bridge.MotionBridge(lambda: (None, self.p))
        self.bridge.import_clip(render('Place_v001.mov'))

    def test_places_on_new_tracks_of_a_copy_and_leaves_source_alone(self):
        result = self.bridge.place_on_copy(self.source, [
            dict(media_id='m1', frame=10, track='Sections'), dict(media_id='m1', frame=90, track='Overlays'),
            dict(media_id='m1', frame=200, track='Sections')], 'V2 copie')
        self.assertTrue(result['complete'])
        self.assertTrue(all(pl['verified'] for pl in result['placed']))
        self.assertEqual(self.source.GetTrackCount('video'), 1)  # source untouched
        self.assertIs(self.p.current, self.source)  # selection restored

    def test_rejects_overlap_on_one_track_before_any_write(self):
        with self.assertRaises(ValueError):
            self.bridge.place_on_copy(self.source, [dict(media_id='m1', frame=10, track='T'), dict(media_id='m1', frame=20, track='T')], 'x')
        self.assertIs(self.p.current, self.source)

if __name__ == '__main__':
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from montage_build import MontageBuilder, folder_for, folder_layout


class Item:
    def __init__(self, media_id, path='', fps='24', frames='480'):
        self.media_id, self.path, self.fps, self.frames = media_id, path, fps, frames

    def GetMediaId(self): return self.media_id
    def GetName(self): return Path(self.path).name or self.media_id
    def GetClipProperty(self, name): return dict(FPS=self.fps, Frames=self.frames).get(name, self.path if name == 'File Path' else None)


class Folder:
    def __init__(self, name='Master'):
        self.name, self.clips, self.folders = name, [], []

    def GetName(self): return self.name
    def GetClipList(self): return self.clips
    def GetSubFolderList(self): return self.folders


class Placed:
    def __init__(self, start, end): self.start, self.end = start, end
    def GetStart(self): return self.start
    def GetEnd(self): return self.end


class Timeline:
    def __init__(self, name): self.name, self.start = name, 86400
    def GetName(self): return self.name
    def GetUniqueId(self): return 'tl-' + self.name
    def GetStartFrame(self): return self.start


class Pool:
    def __init__(self):
        self.root, self.appended, self.refuse_import = Folder(), [], False
        self.current = self.root  # Resolve always has a current folder
        self.project = None

    def GetRootFolder(self): return self.root
    def GetCurrentFolder(self): return self.current
    def SetCurrentFolder(self, folder): self.current = folder; return True

    def AddSubFolder(self, parent, name):
        folder = Folder(name)
        parent.folders.append(folder)
        return folder

    def ImportMedia(self, paths):
        if self.refuse_import:
            return []
        items = [Item(f'm{len(self.imported()) + i}', path) for i, path in enumerate(paths)]
        self.current.clips.extend(items)
        return items

    def imported(self):
        out, todo = [], [self.root]
        while todo:
            f = todo.pop()
            out += f.clips
            todo += f.folders
        return out

    def CreateEmptyTimeline(self, name):
        timeline = Timeline(name)
        self.project.timelines.append(timeline)
        return timeline

    def AppendToTimeline(self, specs):
        spec = specs[0]
        length = spec['endFrame'] - spec['startFrame'] + 1
        self.appended.append(spec)
        return [Placed(spec['recordFrame'] + self.project.shift, spec['recordFrame'] + length)]


class Project:
    def __init__(self):
        self.pool, self.timelines, self.current, self.shift, self.render_calls = Pool(), [], None, 0, []
        self.pool.project = self
        self.status = [{'JobStatus': 'Rendering'}, {'JobStatus': 'Complete'}]
        self.write_file = None

    def GetMediaPool(self): return self.pool
    def GetTimelineCount(self): return len(self.timelines)
    def GetTimelineByIndex(self, i): return self.timelines[i - 1]
    def SetCurrentTimeline(self, t): self.current = t; return True
    def GetCurrentTimeline(self): return self.current
    def SetCurrentRenderFormatAndCodec(self, a, b): self.render_calls.append(('format', a, b)); return True
    def SetRenderSettings(self, s): self.render_calls.append(('settings', s)); self.settings = s; return True
    def AddRenderJob(self): return 'job1'
    def StartRendering(self, jobs, interactive):
        if self.write_file:
            Path(self.settings['TargetDir'], self.settings['CustomName'] + '.mp4').write_bytes(b'video')
        return True
    def GetRenderJobStatus(self, job): return self.status.pop(0) if len(self.status) > 1 else self.status[0]
    def StopRendering(self): self.render_calls.append('stop')


class Layout(unittest.TestCase):
    def test_defaults_and_renaming_by_id(self):
        layout = folder_layout(names={'sound': 'Voix'})
        self.assertEqual([f['name'] for f in layout], ['Sources', 'Animations', 'Musique', 'Voix', 'Montage fini', 'Autres'])

    def test_a_new_kind_of_folder_is_one_more_line(self):
        layout = folder_layout([dict(id='logos', name='Logos', extensions=['.png'])])
        self.assertEqual(folder_for(dict(path='C:/x/a.PNG'), layout)['id'], 'logos')
        self.assertEqual(folder_for(dict(path='C:/x/a.mp4'), layout)['id'], 'other')

    def test_explicit_folder_beats_role_and_unknown_is_refused(self):
        layout = folder_layout()
        self.assertEqual(folder_for(dict(path='a.mp4', role='source', folder='music'), layout)['id'], 'music')
        self.assertEqual(folder_for(dict(path='a.mp4', role='source'), layout)['id'], 'sources')
        with self.assertRaisesRegex(ValueError, 'Unknown folder'):
            folder_for(dict(path='a.mp4', folder='nope'), layout)

    def test_refuses_bad_names_and_duplicates(self):
        with self.assertRaises(ValueError):
            folder_layout(names={'sources': 'a/b'})
        with self.assertRaises(ValueError):
            folder_layout(names={'sound': 'Sources'})
        with self.assertRaises(ValueError):
            folder_layout(names={'missing': 'X'})


class Organize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for name in ('a.mp4', 'intro.mov', 'song.wav', 'note.txt'):
            (self.dir / name).write_bytes(b'x')
        self.project = Project()
        self.builder = MontageBuilder(lambda: (None, self.project), sleep=lambda s: None)

    def tearDown(self):
        self.tmp.cleanup()

    def files(self):
        return [dict(path=str(self.dir / 'a.mp4'), role='source'), dict(path=str(self.dir / 'intro.mov'), role='animation'),
                dict(path=str(self.dir / 'song.wav'), role='music')]

    def test_creates_project_and_role_bins_and_imports_into_them(self):
        result = self.builder.organize_media('Client X', self.files())
        bins = {m['path']: m['bin'] for m in result['media']}
        self.assertEqual(sorted(bins.values()), ['Client X/Animations', 'Client X/Musique', 'Client X/Sources'])
        root = self.project.pool.root
        self.assertEqual([f.name for f in root.folders], ['Client X'])
        self.assertEqual(sorted(f.name for f in root.folders[0].folders), ['Animations', 'Musique', 'Sources'])
        self.assertEqual(len(self.project.pool.imported()), 3)
        self.assertIs(self.project.pool.current, root)  # the folder Resolve had open is restored

    def test_running_twice_reuses_bins_and_clips(self):
        self.builder.organize_media('Client X', self.files())
        again = self.builder.organize_media('Client X', self.files())
        self.assertTrue(all(m['reused'] for m in again['media']))
        self.assertEqual(len(self.project.pool.root.folders), 1)
        self.assertEqual(len(self.project.pool.imported()), 3)

    def test_unlisted_role_goes_to_other_and_custom_name_is_used(self):
        result = self.builder.organize_media('P', [dict(path=str(self.dir / 'a.mp4'), role='mystery')], names={'other': 'Divers'})
        self.assertEqual(result['media'][0]['bin'], 'P/Divers')

    def test_refuses_missing_files_non_media_and_relative_paths(self):
        for path in (str(self.dir / 'nope.mp4'), str(self.dir / 'note.txt'), 'a.mp4'):
            with self.assertRaises(ValueError):
                self.builder.organize_media('P', [dict(path=path)])
        self.assertEqual(self.project.pool.root.folders, [])

    def test_a_failed_import_is_reported(self):
        self.project.pool.refuse_import = True
        with self.assertRaisesRegex(RuntimeError, 'did not import'):
            self.builder.organize_media('P', self.files()[:1])


class Build(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.builder = MontageBuilder(lambda: (None, self.project), sleep=lambda s: None)
        self.clip, self.intro = Item('m1', 'a.mp4', fps='24', frames='480'), Item('m2', 'intro.mov', fps='24', frames='48')
        self.project.pool.root.clips.extend([self.clip, self.intro])
        self.plan = dict(sequence=[
            dict(kind='placed', clip='intro', source='intro.mov', start=0.0, end=2.0, keep_clip_audio=False, final_start=0.0),
            dict(kind='source', clip='main', source='q1', start=0.0, end=4.0, final_start=2.0),
            dict(kind='source', clip='main', source='q1', start=9.0, end=15.0, final_start=6.0),
        ], overlays=[1], fades=[])
        self.sources = {'q1': 'm1', 'intro.mov': 'm2'}

    def test_places_pieces_back_to_back_in_source_frames(self):
        result = self.builder.build_timeline('Montage A', self.plan, self.sources)
        specs = self.project.pool.appended
        self.assertEqual([(s['startFrame'], s['endFrame']) for s in specs], [(0, 47), (0, 95), (216, 359)])
        self.assertEqual([s['recordFrame'] for s in specs], [86400, 86448, 86544])
        self.assertEqual(specs[0]['mediaType'], 1)
        self.assertNotIn('mediaType', specs[1])
        self.assertTrue(result['complete'])
        self.assertEqual(result['frames'], 48 + 96 + 144)
        self.assertEqual(result['not_applied'], {'overlays': 1})

    def test_refuses_an_existing_timeline_name_and_an_empty_plan(self):
        self.builder.build_timeline('Montage A', self.plan, self.sources)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.builder.build_timeline('Montage A', self.plan, self.sources)
        with self.assertRaisesRegex(ValueError, 'nothing to put'):
            self.builder.build_timeline('B', dict(sequence=[]), self.sources)

    def test_refuses_a_source_without_media_before_creating_anything(self):
        with self.assertRaisesRegex(ValueError, 'No media for: q1'):
            self.builder.build_timeline('C', self.plan, {'intro.mov': 'm2'})
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.builder.build_timeline('C', self.plan, {'q1': 'zzz', 'intro.mov': 'm2'})
        self.assertEqual(self.project.timelines, [])

    def test_a_misplaced_piece_stops_and_says_what_was_left(self):
        self.project.shift = 1
        with self.assertRaisesRegex(RuntimeError, 'holds 1 of 3 pieces; do not retry blindly'):
            self.builder.build_timeline('D', self.plan, self.sources)

    def test_finds_media_in_sub_bins(self):
        sub = Folder('Sources')
        sub.clips.append(Item('m3', 'b.mp4'))
        self.project.pool.root.folders.append(sub)
        plan = dict(sequence=[dict(kind='source', clip='main', source='q', start=0.0, end=1.0, final_start=0.0)])
        self.assertTrue(self.builder.build_timeline('E', plan, {'q': 'm3'})['complete'])


class Render(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.project = Project()
        self.project.write_file = True
        self.timeline = Timeline('T')
        self.builder = MontageBuilder(lambda: (None, self.project), sleep=lambda s: None)
        self.find = lambda p, timeline_id: self.timeline

    def tearDown(self):
        self.tmp.cleanup()

    def test_renders_and_returns_the_file(self):
        result = self.builder.render('tl-T', self.find, str(self.dir), 'final')
        self.assertEqual(Path(result['file']).name, 'final.mp4')
        self.assertEqual(result['size'], 5)
        self.assertIn(('format', 'mp4', 'H264'), self.project.render_calls)

    def test_never_overwrites_and_refuses_unsafe_names(self):
        (self.dir / 'final.mp4').write_bytes(b'old')
        with self.assertRaises(FileExistsError):
            self.builder.render('tl-T', self.find, str(self.dir), 'final')
        for name in ('', 'a/b', 'a.mp4'):
            with self.assertRaises(ValueError):
                self.builder.render('tl-T', self.find, str(self.dir), name)
        with self.assertRaises(ValueError):
            self.builder.render('tl-T', self.find, str(self.dir / 'missing'), 'x')

    def test_a_failed_job_and_a_missing_file_are_errors(self):
        self.project.status = [{'JobStatus': 'Failed', 'Error': 'disk full'}]
        with self.assertRaisesRegex(RuntimeError, 'Failed: disk full'):
            self.builder.render('tl-T', self.find, str(self.dir), 'x')
        self.project.status = [{'JobStatus': 'Complete'}]
        self.project.write_file = False
        with self.assertRaisesRegex(RuntimeError, 'missing or empty'):
            self.builder.render('tl-T', self.find, str(self.dir), 'y')

    def test_a_render_that_never_ends_is_stopped(self):
        self.project.status = [{'JobStatus': 'Rendering'}]
        with self.assertRaises(TimeoutError):
            self.builder.render('tl-T', self.find, str(self.dir), 'z', timeout_seconds=0)
        self.assertIn('stop', self.project.render_calls)


if __name__ == '__main__':
    unittest.main()

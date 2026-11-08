import json
import tempfile
import unittest
from pathlib import Path

from auto_edit_tools import find_media, load_sources, media_facts, media_words, register, timeline_keeps


class Media:
    def __init__(self, media_id, transcription=None):
        self.media_id, self.transcription = media_id, transcription
        self.transcribed = 0

    def GetMediaId(self):
        return self.media_id

    def GetClipProperty(self, name):
        return dict(FPS='24', Frames='360').get(name, '01:00:00:00' if name == 'Start TC' else None)

    def GetTranscription(self):
        return self.transcription

    def TranscribeAudio(self):
        self.transcribed += 1
        return True


class Folder:
    def __init__(self, clips=(), folders=()):
        self.clips, self.folders = list(clips), list(folders)

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return self.folders


class Project:
    def __init__(self, root):
        self.root = root

    def GetMediaPool(self):
        return self

    def GetRootFolder(self):
        return self.root


class Clip:
    def __init__(self, media, start, source_start, source_end):
        self.media, self.start, self.source_start, self.source_end = media, start, source_start, source_end

    def GetMediaPoolItem(self):
        return self.media

    def GetStart(self):
        return self.start

    def GetSourceStartFrame(self):
        return self.source_start

    def GetSourceEndFrame(self):
        return self.source_end


class Timeline:
    def __init__(self, clips):
        self.clips = clips

    def GetItemListInTrack(self, kind, track):
        return self.clips if kind == 'video' and track == 1 else []


class FakeMcp:
    def __init__(self):
        self.tools, self.annotations = {}, {}

    def tool(self, annotations):
        def add(fn):
            self.tools[fn.__name__] = fn
            self.annotations[fn.__name__] = annotations
            return fn
        return add


TRANSCRIPTION = dict(language='fr', segments=[dict(words=[
    dict(start='01:00:02:00', end='01:00:04:00', text='bonjour'), dict(start='01:00:04:00', end='01:00:09:00', text='(...)'),
    dict(start='01:00:09:00', end='01:00:11:00', text='merci')])])
SILENCE = dict(type='silence', params=dict(min_pause_seconds=1, keep_pause_seconds=0))
RECIPE = dict(version=2, clips=[dict(id='main', source=dict(type='field', field='video'), treatments=[SILENCE])])
SOURCES = dict(video=dict(media_id='m1'))


class Tools(unittest.TestCase):
    def setUp(self):
        self.media = Media('m1', TRANSCRIPTION)
        self.other = Media('m2')
        self.project = Project(Folder([self.other], [Folder([self.media])]))
        self.timeline = Timeline([Clip(self.media, 0, 0, 96), Clip(self.other, 96, 0, 10), Clip(self.media, 106, 216, 360)])
        self.mcp = FakeMcp()

        class Lock:
            def __enter__(self): return self
            def __exit__(self, *args): return False
        register(self.mcp, Lock(), lambda: (None, self.project), lambda p, timeline_id: self.timeline, 'read', 'write')

    def test_finds_media_in_subfolders_and_refuses_an_unknown_id(self):
        self.assertIs(find_media(self.project, 'm1'), self.media)
        with self.assertRaises(ValueError):
            find_media(self.project, 'missing')

    def test_reads_duration_and_rate(self):
        self.assertEqual(media_facts(self.media), dict(fps=24.0, duration=15.0, start_timecode='01:00:00:00'))

    def test_only_transcribing_is_declared_as_a_write(self):
        writes = [name for name, annotation in self.mcp.annotations.items() if annotation == 'write']
        self.assertEqual(writes, ['transcribe_media_for_edit'])

    def test_preview_uses_resolve_words_and_creates_nothing(self):
        result = self.mcp.tools['preview_auto_edit'](RECIPE, SOURCES)
        self.assertEqual(result['transcripts'], dict(video='resolve'))
        self.assertEqual(result['plan']['clips']['main']['keeps'], [[0.0, 4.0], [9.0, 15.0]])
        self.assertEqual(self.media.transcribed, 0)

    def test_explains_a_media_without_transcription(self):
        with self.assertRaisesRegex(ValueError, 'needs a transcription'):
            self.mcp.tools['preview_auto_edit'](RECIPE, dict(video=dict(media_id='m2')))

    def test_a_fixed_clip_needs_no_transcription(self):
        recipe = dict(version=2, clips=[dict(id='intro', source=dict(type='file', file='intro.mov')), RECIPE['clips'][0]])
        result = self.mcp.tools['preview_auto_edit'](recipe, {'intro.mov': dict(media_id='m2'), 'video': dict(media_id='m1')})
        self.assertEqual(result['transcripts'], {'intro.mov': 'none', 'video': 'resolve'})
        self.assertEqual([item['clip'] for item in result['plan']['sequence']], ['intro', 'main', 'main'])

    def test_an_unknown_media_stops_the_preview(self):
        with self.assertRaisesRegex(ValueError, 'not found'):
            load_sources(self.project, dict(video=dict(media_id='missing')))

    def test_compares_with_the_hand_edit_of_that_media_only(self):
        result = self.mcp.tools['compare_auto_edit'](RECIPE, SOURCES, 'main', 'timeline')
        self.assertEqual(result['comparison']['matched'], 4)
        self.assertEqual(result['comparison']['median_ms'], 0)
        self.assertEqual(result['reference_duration'], 10)
        self.assertEqual(result['planned_duration'], 10)

    def test_refuses_comparing_a_clip_absent_from_the_plan(self):
        with self.assertRaisesRegex(ValueError, 'not in this plan'):
            self.mcp.tools['compare_auto_edit'](RECIPE, SOURCES, 'other', 'timeline')

    def test_refuses_a_timeline_without_this_media(self):
        with self.assertRaisesRegex(ValueError, 'No clip of this media'):
            timeline_keeps(Timeline([Clip(self.other, 0, 0, 10)]), 'm1', 24)

    def test_uses_a_whisper_transcript_when_given(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'voix.json'
            path.write_text(json.dumps(dict(segments=[dict(words=[dict(start=2, end=4, text='a'), dict(start=9, end=11, text='b')])])), encoding='utf8')
            words, _, source = media_words(self.other, str(path))
            self.assertEqual(source, 'whisper')
            self.assertEqual(len(words), 2)
            with self.assertRaisesRegex(ValueError, 'JSON'):
                media_words(self.other, str(Path(folder) / 'voix.txt'))


if __name__ == '__main__':
    unittest.main()

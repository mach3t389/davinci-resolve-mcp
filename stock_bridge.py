"""Stock footage (b-roll) of a video project: import into a "Stock" bin, place segments on NEW tracks of a timeline copy.

Only video files (.mp4/.mov) inside an ``Assets`` folder that belongs to a video project are accepted: the project is
the folder next to which ``Motion/.motion-project`` exists (``<project>\\<anything>\\Assets\\Videos\\clip.mp4`` with
``<project>\\Motion\\.motion-project``). The Media Pool folder named like the project holds the "Stock" bin.
Nothing is ever deleted or moved; the source timeline is never edited (a duplicate receives the clips)."""
import time
from pathlib import Path

from motion_bridge import MARKER, project_of, same

BIN = 'Stock'
EXTENSIONS = {'.mp4', '.mov'}

def project_name(file_path):
    """Video project of a stock file, or raise. Walks up to an ``Assets`` folder, then looks for ``Motion/.motion-project``
    beside it or one level higher."""
    path = Path(file_path).resolve()
    if path.suffix.lower() not in EXTENSIONS:
        raise ValueError(f'Only {sorted(EXTENSIONS)} stock videos are allowed.')
    if not path.is_file():
        raise ValueError(f'Stock file not found: {path}')
    for parent in path.parents:
        if parent.name.lower() == 'assets':
            for root in (parent.parent, parent.parent.parent):
                if (root / 'Motion' / MARKER).is_file():
                    return root.name
            break
    raise ValueError('Stock videos must live in an "Assets" folder of a video project that has a Motion project (.motion-project).')

class StockBridge:
    def __init__(self, project):
        self.project = project

    def _folder(self, pool, name, create):
        parents = [f for f in (pool.GetRootFolder().GetSubFolderList() or []) if f.GetName() == name]
        if len(parents) != 1:
            raise ValueError(f'The Media Pool needs exactly one folder named "{name}" (the video project).')
        for s in parents[0].GetSubFolderList() or []:
            if s.GetName() == BIN:
                return s
        if not create:
            raise ValueError(f'No "{BIN}" bin in "{name}" yet.')
        folder = pool.AddSubFolder(parents[0], BIN)
        if not folder:
            raise RuntimeError(f'Resolve did not create the "{BIN}" bin.')
        return folder

    def clips(self, name):
        _, p = self.project()
        folder = self._folder(p.GetMediaPool(), name, create=False)
        return dict(project=name, clips=[dict(media_id=c.GetMediaId(), name=c.GetName(), file=c.GetClipProperty('File Path'),
                    fps=c.GetClipProperty('FPS'), frames=c.GetClipProperty('Frames')) for c in (folder.GetClipList() or [])])

    def _item(self, pool, folder, path):
        for c in folder.GetClipList() or []:
            if same(c.GetClipProperty('File Path'), path):
                return c
        previous = pool.GetCurrentFolder()
        pool.SetCurrentFolder(folder)
        try:
            items = pool.ImportMedia([str(path)]) or []
        finally:
            pool.SetCurrentFolder(previous)
        if len(items) != 1:
            raise RuntimeError(f'Resolve did not import {path.name}.')
        return items[0]

    def place_on_copy(self, timeline, placements, copy_name):
        """Each placement: {"file_path", "frame" (timeline frames from start), "track" (new track name),
        "start_seconds" (in-point inside the clip), "duration_seconds"}. Video only (no audio)."""
        _, p = self.project()
        pool = p.GetMediaPool()
        names = {project_name(pl['file_path']) for pl in placements}
        if len(names) != 1:
            raise ValueError(f'Refused: the files come from several projects: {sorted(names)}.')
        name = names.pop()
        # The timeline must not already hold Motion clips of another video project.
        for i in range(1, timeline.GetTrackCount('video') + 1):
            for c in timeline.GetItemListInTrack('video', i) or []:
                mp = c.GetMediaPoolItem()
                fp = mp.GetClipProperty('File Path') if mp else ''
                if fp and project_of(fp) and project_of(fp) != name:
                    raise ValueError(f'Refused: the timeline uses Motion clips of {project_of(fp)!r}, not {name!r}.')
        fps = float(timeline.GetSetting('timelineFrameRate'))
        length = timeline.GetEndFrame() - timeline.GetStartFrame()
        spans = {}
        for pl in placements:
            frame = int(pl['frame'])
            dur = round(float(pl['duration_seconds']) * fps)
            if not 0 <= frame < length or dur < 1:
                raise ValueError(f'Placement outside the timeline or empty (frame {frame}).')
            for s, e in spans.get(pl['track'], []):
                if frame < e and s < frame + dur:
                    raise ValueError(f"Clips overlap on track {pl['track']!r} at frame {frame}.")
            spans.setdefault(pl['track'], []).append((frame, frame + dur))

        folder = self._folder(pool, name, create=True)
        items = {pl['file_path']: self._item(pool, folder, Path(pl['file_path']).resolve()) for pl in placements}
        for pl in placements:  # segment must lie inside the clip (checked before any change to a timeline)
            it = items[pl['file_path']]
            cfps, frames = float(it.GetClipProperty('FPS')), int(it.GetClipProperty('Frames'))
            first = round(float(pl['start_seconds']) * cfps)
            last = first + round(float(pl['duration_seconds']) * cfps) - 1
            if first < 0 or last >= frames:
                raise ValueError(f"{it.GetName()}: segment {pl['start_seconds']}s + {pl['duration_seconds']}s exceeds the clip ({frames / cfps:.1f}s).")

        def snapshot(t):
            return [(kind, i, [(c.GetName(), c.GetStart(), c.GetEnd()) for c in (t.GetItemListInTrack(kind, i) or [])])
                    for kind in ('video', 'audio') for i in range(1, t.GetTrackCount(kind) + 1)]

        before = snapshot(timeline)
        original = p.GetCurrentTimeline()
        copy = timeline.DuplicateTimeline(copy_name)
        if not copy or copy.GetUniqueId() == timeline.GetUniqueId():
            raise RuntimeError('Resolve did not create the timeline copy.')
        result = dict(copy_id=copy.GetUniqueId(), copy_name=copy.GetName(), placed=[], complete=False)
        try:
            p.SetCurrentTimeline(copy)
            for _ in range(20):
                now = p.GetCurrentTimeline()
                if now and now.GetUniqueId() == copy.GetUniqueId():
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError('Resolve did not switch to the timeline copy.')
            time.sleep(1)
            tracks = {}
            for tname in dict.fromkeys(pl['track'] for pl in placements):
                if not copy.AddTrack('video'):
                    raise RuntimeError(f'Could not add track {tname!r}.')
                tracks[tname] = copy.GetTrackCount('video')
                copy.SetTrackName('video', tracks[tname], tname)
            for pl in placements:
                it = items[pl['file_path']]
                cfps = float(it.GetClipProperty('FPS'))
                first = round(float(pl['start_seconds']) * cfps)
                last = first + round(float(pl['duration_seconds']) * cfps) - 1
                record = copy.GetStartFrame() + int(pl['frame'])
                made = pool.AppendToTimeline([{'mediaPoolItem': it, 'startFrame': first, 'endFrame': last,
                                               'trackIndex': tracks[pl['track']], 'recordFrame': record, 'mediaType': 1}]) or []
                ok = len(made) == 1 and made[0].GetStart() == record
                result['placed'].append(dict(name=it.GetName(), track=pl['track'], frame=int(pl['frame']), source_in_s=pl['start_seconds'],
                                             seconds=pl['duration_seconds'], verified=ok))
                if not ok:
                    raise RuntimeError(f"{it.GetName()} was not placed at frame {pl['frame']}.")
            after = snapshot(copy)
            result['existing_tracks_unchanged'] = [x for x in after if not (x[0] == 'video' and x[1] in tracks.values())] == before
            result['markers_kept'] = len(copy.GetMarkers() or {}) == len(timeline.GetMarkers() or {})
            result['complete'] = result['existing_tracks_unchanged'] and result['markers_kept']
        finally:
            if original:
                p.SetCurrentTimeline(original)
        return result

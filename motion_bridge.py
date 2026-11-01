"""Import and relink Remotion renders. Only <project>/out/*.mov of a marked Motion project, only clips in the Motion bin.

out/ holds the versions Resolve currently uses; replaced versions move to out/anciennes-versions/ once Resolve
no longer points to them (never before, so no clip goes offline)."""
import shutil
from pathlib import Path

BIN = 'Motion'
MARKER = '.motion-project'  # created at the root of every Motion project; makes paths machine-independent
ARCHIVE = 'anciennes-versions'

def out_dir(path):
    if path.parent.name == 'out':
        return path.parent
    if path.parent.name == ARCHIVE and path.parent.parent.name == 'out':
        return path.parent.parent
    return None

def allowed(file_path):
    path = Path(file_path).resolve()
    out = out_dir(path)
    if path.suffix.lower() != '.mov' or out is None or not (out.parent / MARKER).is_file():
        raise ValueError(f'Only .mov renders in out/ (or out/{ARCHIVE}/) of a Motion project (with {MARKER}) are allowed.')
    if not path.is_file():
        raise ValueError('Render file not found.')
    return path

def move(path, folder):
    """Move without ever overwriting; returns the new path, or raises."""
    target = folder / path.name
    if target.exists():
        raise FileExistsError(f'{target} already exists')
    folder.mkdir(exist_ok=True)
    return Path(shutil.move(str(path), str(target)))

def same(a, b):
    return Path(a).resolve() == Path(b).resolve()

class MotionBridge:
    def __init__(self, project):
        self.project = project

    def _bin(self, pool, create):
        root = pool.GetRootFolder()
        found = [f for f in (root.GetSubFolderList() or []) if f.GetName() == BIN]
        if found:
            return found[0]
        if not create:
            raise ValueError(f'No "{BIN}" bin in the Media Pool; import a render first.')
        folder = pool.AddSubFolder(root, BIN)
        if not folder:
            raise RuntimeError('Resolve did not create the Motion bin.')
        return folder

    def _item(self, folder, media_id):
        matches = [c for c in (folder.GetClipList() or []) if c.GetMediaId() == media_id]
        if len(matches) != 1:
            raise ValueError(f'Media ID not found in the "{BIN}" bin.')
        return matches[0]

    def clips(self):
        _, p = self.project()
        folder = self._bin(p.GetMediaPool(), create=False)
        return dict(bin=BIN, clips=[dict(media_id=c.GetMediaId(), name=c.GetName(),
                    file=c.GetClipProperty('File Path')) for c in (folder.GetClipList() or [])])

    def import_clip(self, file_path):
        path = allowed(file_path)
        _, p = self.project()
        pool = p.GetMediaPool()
        folder = self._bin(pool, create=True)
        if any(same(c.GetClipProperty('File Path'), path) for c in (folder.GetClipList() or [])):
            raise ValueError('This render is already in the Motion bin.')
        previous = pool.GetCurrentFolder()
        pool.SetCurrentFolder(folder)
        try:
            items = pool.ImportMedia([str(path)]) or []
        finally:
            pool.SetCurrentFolder(previous)
        if len(items) != 1:
            raise RuntimeError('Resolve did not import exactly one clip.')
        item = items[0]
        return dict(media_id=item.GetMediaId(), name=item.GetName(), file=item.GetClipProperty('File Path'),
                    fps=item.GetClipProperty('FPS'), bin=BIN)

    def place_on_copy(self, timeline, placements, copy_name):
        """Duplicate `timeline`, add one new video track per distinct `track` name and put each Motion clip at its
        frame (offset from timeline start). Existing tracks are never edited; the copy is checked against the source."""
        _, p = self.project()
        pool = p.GetMediaPool()
        folder = self._bin(pool, create=False)
        items = {pl['media_id']: self._item(folder, pl['media_id']) for pl in placements}
        length = timeline.GetEndFrame() - timeline.GetStartFrame()
        spans = {}
        for pl in placements:
            dur = int(items[pl['media_id']].GetClipProperty('Frames'))
            if not 0 <= int(pl['frame']) < length:
                raise ValueError(f"Frame {pl['frame']} is outside the timeline.")
            for s, e in spans.get(pl['track'], []):
                if pl['frame'] < e and s < pl['frame'] + dur:
                    raise ValueError(f"Clips overlap on track {pl['track']!r} at frame {pl['frame']}.")
            spans.setdefault(pl['track'], []).append((int(pl['frame']), int(pl['frame']) + dur))

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
            p.SetCurrentTimeline(copy)  # AppendToTimeline targets the current timeline
            tracks = {}
            for name in dict.fromkeys(pl['track'] for pl in placements):
                if not copy.AddTrack('video'):
                    raise RuntimeError(f'Could not add track {name!r}.')
                tracks[name] = copy.GetTrackCount('video')
                copy.SetTrackName('video', tracks[name], name)
            for pl in placements:
                item = items[pl['media_id']]
                record = copy.GetStartFrame() + int(pl['frame'])
                made = pool.AppendToTimeline([{'mediaPoolItem': item, 'startFrame': 0, 'endFrame': int(item.GetClipProperty('Frames')) - 1,
                                               'trackIndex': tracks[pl['track']], 'recordFrame': record, 'mediaType': 1}]) or []
                ok = len(made) == 1 and made[0].GetStart() == record
                result['placed'].append(dict(name=item.GetName(), track=pl['track'], frame=int(pl['frame']), verified=ok))
                if not ok:
                    raise RuntimeError(f"{item.GetName()} was not placed at frame {pl['frame']}.")
            after = snapshot(copy)
            result['existing_tracks_unchanged'] = [x for x in after if not (x[0] == 'video' and x[1] in tracks.values())] == before
            result['markers_kept'] = len(copy.GetMarkers() or {}) == len(timeline.GetMarkers() or {})
            result['complete'] = result['existing_tracks_unchanged'] and result['markers_kept']
        finally:
            if original:
                p.SetCurrentTimeline(original)
        return result

    def replace_clip(self, media_id, file_path):
        path = allowed(file_path)
        _, p = self.project()
        folder = self._bin(p.GetMediaPool(), create=False)
        item = self._item(folder, media_id)
        before = item.GetClipProperty('File Path')
        allowed(before)  # never relink a clip that did not come from a Motion project renders folder
        if same(before, path):
            raise ValueError('Clip already points to this file.')
        if path.parent.name == ARCHIVE:  # undo: an archived version becomes current again
            path = move(path, path.parent.parent)
        ok = item.ReplaceClip(str(path))
        after = item.GetClipProperty('File Path')
        verified = bool(ok and same(after, path))
        result = dict(success=bool(ok), verified=verified, media_id=media_id, previous_file=before, file=after, archived=False)
        old = Path(before).resolve()
        still_used = any(same(c.GetClipProperty('File Path'), old) for c in (folder.GetClipList() or []))
        if verified and old.parent.name == 'out' and not still_used:
            try:
                result.update(previous_file=str(move(old, old.parent / ARCHIVE)), archived=True)
            except OSError as e:  # e.g. still locked by Resolve: the file stays in out/, nothing is lost
                result['archive_error'] = str(e)
        # Replacing back with `previous_file` undoes the change; no file is ever deleted.
        return result

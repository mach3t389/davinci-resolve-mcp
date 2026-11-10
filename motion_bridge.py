"""Import and relink Remotion renders. Only <project>/out/*.mov of a marked Motion project, only clips in the Motion bin.

out/ holds the versions Resolve currently uses; replaced versions move to out/anciennes-versions/ once Resolve
no longer points to them (never before, so no clip goes offline)."""
import shutil
import time
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

def project_of(path):
    """Video project name of a render: ...\\RM Charest\\Motion\\out\\x.mov -> "RM Charest"; None outside a Motion project."""
    out = out_dir(Path(path).resolve())
    return out.parent.parent.name if out else None

class MotionBridge:
    def __init__(self, project):
        self.project = project

    # One "Motion" bin per video project, inside that project's Media Pool folder (Master/<Project>/Motion).
    # The project name is the folder that holds the Motion project: ...\RM Charest\Motion\out -> "RM Charest".
    def _bins(self, pool):
        bins = {}
        for f in pool.GetRootFolder().GetSubFolderList() or []:
            for s in f.GetSubFolderList() or []:
                if s.GetName() == BIN:
                    bins[f.GetName()] = s
        return bins

    def _bin(self, pool, name, create):
        found = self._bins(pool).get(name)
        if found:
            return found
        if not create:
            raise ValueError(f'No "{BIN}" bin in the Media Pool folder "{name}"; import a render first.')
        parents = [f for f in (pool.GetRootFolder().GetSubFolderList() or []) if f.GetName() == name]
        if len(parents) != 1:
            raise ValueError(f'The Media Pool needs exactly one folder named "{name}" (the video project) to hold its Motion bin.')
        folder = pool.AddSubFolder(parents[0], BIN)
        if not folder:
            raise RuntimeError(f'Resolve did not create the Motion bin in "{name}".')
        return folder

    def _find(self, pool, media_id):
        """(project, bin folder, item) of a media ID, searched in every project's Motion bin."""
        matches = [(name, folder, c) for name, folder in self._bins(pool).items()
                   for c in (folder.GetClipList() or []) if c.GetMediaId() == media_id]
        if len(matches) != 1:
            raise ValueError(f'Media ID not found in a project "{BIN}" bin.')
        return matches[0]

    def clips(self):
        _, p = self.project()
        pool = p.GetMediaPool()
        return dict(clips=[dict(project=name, media_id=c.GetMediaId(), name=c.GetName(),
                    file=c.GetClipProperty('File Path'))
                    for name, folder in self._bins(pool).items() for c in (folder.GetClipList() or [])])

    def import_clip(self, file_path):
        path = allowed(file_path)
        name = out_dir(path).parent.parent.name
        _, p = self.project()
        pool = p.GetMediaPool()
        folder = self._bin(pool, name, create=True)
        if any(same(c.GetClipProperty('File Path'), path) for c in (folder.GetClipList() or [])):
            raise ValueError(f'This render is already in the "{name}/{BIN}" bin.')
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
                    fps=item.GetClipProperty('FPS'), bin=f'{name}/{BIN}')

    def place_on_copy(self, timeline, placements, copy_name):
        """Duplicate `timeline`, add one new video track per distinct `track` name and put each Motion clip at its
        frame (offset from timeline start). Existing tracks are never edited; the copy is checked against the source."""
        _, p = self.project()
        pool = p.GetMediaPool()
        items = {pl['media_id']: self._find(pool, pl['media_id'])[2] for pl in placements}
        # Never mix two video projects: every clip comes from one project, and the timeline must not already hold another's.
        wanted = {project_of(i.GetClipProperty('File Path')) for i in items.values()}
        if len(wanted) != 1:
            raise ValueError(f'Refused: the clips come from several projects: {sorted(map(str, wanted))}.')
        present = set()
        for kind_track in range(1, timeline.GetTrackCount('video') + 1):
            for c in timeline.GetItemListInTrack('video', kind_track) or []:
                mp = c.GetMediaPoolItem()
                fp = mp.GetClipProperty('File Path') if mp else ''
                if fp and project_of(fp):
                    present.add(project_of(fp))
        if present and present != wanted:
            raise ValueError(f'Refused: the timeline already uses Motion clips of {sorted(present)}, '
                             f'but these clips belong to {sorted(wanted)}.')
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
            # Resolve switches asynchronously: adding a track right away was refused once ("Could not add track").
            for _ in range(20):
                now = p.GetCurrentTimeline()
                if now and now.GetUniqueId() == copy.GetUniqueId():
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError('Resolve did not switch to the timeline copy.')
            time.sleep(1)
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
        _, folder, item = self._find(p.GetMediaPool(), media_id)
        before = item.GetClipProperty('File Path')
        old_path = allowed(before)  # never relink a clip that did not come from a Motion project renders folder
        if same(before, path):
            raise ValueError('Clip already points to this file.')
        if out_dir(old_path) != out_dir(path):  # two video projects can share file names (Anim11_v001 ...)
            raise ValueError(f'Refused: this clip belongs to project "{project_of(old_path)}" but the new file belongs to '
                             f'"{project_of(path)}". A clip is only replaced by a newer render of the same project.')
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

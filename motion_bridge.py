"""Import and relink Remotion renders. Only files under MOTION_OUT_DIR, only clips in the Motion bin."""
import os
from pathlib import Path

ROOT = Path(os.environ.get('MOTION_OUT_DIR', 'D:/Vibe Coding/Motion/out')).resolve()
BIN = 'Motion'

def allowed(file_path):
    path = Path(file_path).resolve()
    if path.suffix.lower() != '.mov' or ROOT not in path.parents:
        raise ValueError(f'Only .mov renders under {ROOT} are allowed.')
    if not path.is_file():
        raise ValueError('Render file not found.')
    return path

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
        return dict(bin=BIN, root=str(ROOT), clips=[dict(media_id=c.GetMediaId(), name=c.GetName(),
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

    def replace_clip(self, media_id, file_path):
        path = allowed(file_path)
        _, p = self.project()
        item = self._item(self._bin(p.GetMediaPool(), create=False), media_id)
        before = item.GetClipProperty('File Path')
        allowed(before)  # never relink a clip that did not come from the Motion renders folder
        if same(before, path):
            raise ValueError('Clip already points to this file.')
        ok = item.ReplaceClip(str(path))
        after = item.GetClipProperty('File Path')
        # Previous render stays on disk: replacing back with `previous_file` undoes the change.
        return dict(success=bool(ok), verified=bool(ok and same(after, path)), media_id=media_id,
                    previous_file=before, file=after)

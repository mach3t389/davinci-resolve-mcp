"""Put a montage plan into Resolve: organise the media in bins, build a NEW timeline, render it.

Nothing existing is edited: bins are reused when they exist, a timeline is only created under a free name, and a
render never overwrites a file. The plan comes from `plan_edit` (see auto_edit.py); its `sequence` lists the pieces."""
import os
import time
from pathlib import Path

MEDIA_SUFFIXES = {'.mp4', '.mov', '.mxf', '.mkv', '.avi', '.m4v', '.webm', '.mp3', '.wav', '.aif', '.aiff', '.m4a', '.flac', '.png', '.jpg', '.jpeg'}
AUDIO_SUFFIXES = {'.mp3', '.wav', '.aif', '.aiff', '.m4a', '.flac'}
OTHER = 'other'

# A folder takes the files whose `role` it lists, then those whose extension it lists. Anything else goes to "other".
# Adding a kind of folder is adding a line here (or passing `folders`); nothing else reads this list.
DEFAULT_FOLDERS = [
    dict(id='sources', name='Sources', roles=['source']),
    dict(id='animations', name='Animations', roles=['animation']),
    dict(id='music', name='Musique', roles=['music']),
    dict(id='sound', name='Son', roles=['sound']),
    dict(id='final', name='Montage fini', roles=['final']),
    dict(id=OTHER, name='Autres', roles=[]),
]


def folder_layout(folders=None, names=None):
    """The folders to use: `folders` replaces the defaults, `names` renames some by id. Validated, 'other' always present."""
    layout = [dict(f, roles=list(f.get('roles', [])), extensions=[e.lower() for e in f.get('extensions', [])])
              for f in (folders if folders is not None else DEFAULT_FOLDERS)]
    for folder in layout:
        if not isinstance(folder.get('id'), str) or not folder['id']:
            raise ValueError('Every folder needs an id.')
    for key, name in (names or {}).items():
        match = [f for f in layout if f['id'] == key]
        if not match:
            raise ValueError(f'No folder {key!r} to rename.')
        match[0]['name'] = name
    if not any(f['id'] == OTHER for f in layout):
        layout.append(dict(id=OTHER, name='Autres', roles=[], extensions=[]))
    for folder in layout:
        name = folder.get('name')
        if not isinstance(name, str) or not name.strip() or '/' in name or '\\' in name:
            raise ValueError(f"Folder {folder['id']!r} needs a plain name.")
        folder['name'] = name.strip()
    if len({f['id'] for f in layout}) != len(layout) or len({f['name'].lower() for f in layout}) != len(layout):
        raise ValueError('Folder ids and names must be unique.')
    return layout


def folder_for(entry, layout):
    """Which folder takes this file: an explicit `folder` id, else its role, else its extension, else 'other'."""
    ids = {f['id']: f for f in layout}
    if entry.get('folder') is not None:
        if entry['folder'] not in ids:
            raise ValueError(f"Unknown folder {entry['folder']!r}.")
        return ids[entry['folder']]
    for folder in layout:
        if entry.get('role') in folder['roles']:
            return folder
    suffix = Path(entry['path']).suffix.lower()
    for folder in layout:
        if suffix in folder['extensions']:
            return folder
    return ids[OTHER]


def same(a, b):
    return Path(a).resolve() == Path(b).resolve()


class MontageBuilder:
    def __init__(self, project, sleep=time.sleep):
        self.project, self.sleep = project, sleep

    # ---- bins -------------------------------------------------------------------------------------------------
    def _subfolder(self, pool, parent, name):
        found = [f for f in (parent.GetSubFolderList() or []) if f.GetName() == name]
        if found:
            return found[0]
        made = pool.AddSubFolder(parent, name)
        if not made:
            raise RuntimeError(f'Resolve did not create the bin {name!r}.')
        return made

    def organize_media(self, project_name, files, folders=None, names=None):
        """Import `files` ({path, role?, folder?}) into <project_name>/<folder>. Existing bins and clips are reused."""
        if not isinstance(project_name, str) or not project_name.strip() or '/' in project_name or '\\' in project_name:
            raise ValueError('The project bin needs a plain name.')
        layout = folder_layout(folders, names)
        plan = []
        for entry in files:
            path = Path(entry['path'])
            if not path.is_absolute() or not path.is_file():
                raise ValueError(f'Media file not found: {path}')
            if path.suffix.lower() not in MEDIA_SUFFIXES:
                raise ValueError(f'Not a media file: {path.name}')
            plan.append((path, folder_for(entry, layout)))
        _, p = self.project()
        pool = p.GetMediaPool()
        root = self._subfolder(pool, pool.GetRootFolder(), project_name.strip())
        previous = pool.GetCurrentFolder()
        media = []
        try:
            for path, folder in plan:
                target = self._subfolder(pool, root, folder['name'])
                existing = [c for c in (target.GetClipList() or []) if same(c.GetClipProperty('File Path') or '.', path)]
                if existing:
                    item, reused = existing[0], True
                else:
                    pool.SetCurrentFolder(target)
                    items = pool.ImportMedia([str(path)]) or []
                    if len(items) != 1:
                        raise RuntimeError(f'Resolve did not import {path.name}.')
                    item, reused = items[0], False
                media.append(dict(path=str(path), media_id=item.GetMediaId(), name=item.GetName(), folder=folder['id'],
                                  bin=f"{project_name.strip()}/{folder['name']}", reused=reused))
        finally:
            if previous:
                pool.SetCurrentFolder(previous)
        return dict(project_bin=project_name.strip(), media=media)

    # ---- timeline ---------------------------------------------------------------------------------------------
    def _timeline_names(self, p):
        return {p.GetTimelineByIndex(i).GetName() for i in range(1, (p.GetTimelineCount() or 0) + 1)}

    def build_timeline(self, name, plan, sources):
        """Create a NEW timeline `name` holding plan['sequence'] back to back.
        `sources` maps each sequence `source` (form question or file name) to a media id of the pool."""
        sequence = plan.get('sequence') or []
        if not sequence:
            raise ValueError('The plan has nothing to put on a timeline.')
        _, p = self.project()
        if name in self._timeline_names(p):
            raise ValueError(f'A timeline named {name!r} already exists; choose another name.')
        pool = p.GetMediaPool()
        items = {}
        for source, media_id in sources.items():
            items[source] = self._media(pool.GetRootFolder(), media_id)
        missing = sorted({piece['source'] for piece in sequence} - set(items))
        if missing:
            raise ValueError(f'No media for: {", ".join(missing)}.')
        timeline = pool.CreateEmptyTimeline(name)
        if not timeline:
            raise RuntimeError('Resolve did not create the timeline.')
        p.SetCurrentTimeline(timeline)
        for _ in range(20):  # Resolve switches asynchronously, and AppendToTimeline targets the current timeline
            now = p.GetCurrentTimeline()
            if now and now.GetUniqueId() == timeline.GetUniqueId():
                break
            self.sleep(0.25)
        else:
            raise RuntimeError(f'Resolve did not switch to the new timeline {name!r}; nothing was placed.')
        record, placed = timeline.GetStartFrame(), []
        try:
            for index, piece in enumerate(sequence):
                item = items[piece['source']]
                fps = float(item.GetClipProperty('FPS'))
                frames = int(item.GetClipProperty('Frames'))
                first = max(0, round(piece['start'] * fps))
                last = min(frames, round(piece['end'] * fps)) - 1  # inclusive, like the other bridges
                if last < first:
                    raise ValueError(f"Piece {index + 1} ({piece['clip']}) is empty after rounding to frames.")
                spec = {'mediaPoolItem': item, 'startFrame': first, 'endFrame': last, 'recordFrame': record}
                if piece['kind'] == 'placed' and piece.get('keep_clip_audio') is False:
                    spec['mediaType'] = 1  # picture only
                made = pool.AppendToTimeline([spec]) or []
                ok = len(made) == 1 and made[0].GetStart() == record
                placed.append(dict(clip=piece['clip'], source=piece['source'], start_frame=first, end_frame=last, record_frame=record, verified=bool(ok)))
                if not ok:
                    raise RuntimeError(f"Piece {index + 1} ({piece['clip']}) was not placed at frame {record}.")
                record = made[0].GetEnd()
        except Exception as error:
            raise RuntimeError(f'{error} The timeline {name!r} ({timeline.GetUniqueId()}) holds {len(placed)} of {len(sequence)} pieces; do not retry blindly.') from error
        return dict(timeline_id=timeline.GetUniqueId(), timeline_name=timeline.GetName(), pieces=len(placed), placed=placed,
                    frames=record - timeline.GetStartFrame(), complete=all(x['verified'] for x in placed) and len(placed) == len(sequence),
                    not_applied=self._not_applied(plan))

    @staticmethod
    def _not_applied(plan):
        """What the plan asks for that this tool does not do yet, so a missing effect is never silent."""
        names = ('overlays', 'fades', 'audio', 'markers', 'chapters')
        return {key: len(plan.get(key) or []) for key in names if plan.get(key)}

    def _media(self, folder, media_id):
        for clip in folder.GetClipList() or []:
            if clip.GetMediaId() == media_id:
                return clip
        for sub in folder.GetSubFolderList() or []:
            try:
                return self._media(sub, media_id)
            except ValueError:
                continue
        raise ValueError(f'Media {media_id!r} not found in the Media Pool.')

    # ---- transcription ----------------------------------------------------------------------------------------
    def transcribe(self, media_id, timeout_seconds=1800):
        """Ask Resolve to transcribe a media (unless it already is) and return its words in source seconds."""
        from auto_edit import words_from_resolve
        _, p = self.project()
        item = self._media(p.GetMediaPool().GetRootFolder(), media_id)
        fps = float(item.GetClipProperty('FPS'))
        start_tc = item.GetClipProperty('Start TC') or '00:00:00:00'

        def words():
            return words_from_resolve(item.GetTranscription() or {}, fps, start_tc)
        if not words():
            if not item.TranscribeAudio():
                raise RuntimeError('Resolve did not start the transcription.')
            deadline = time.monotonic() + timeout_seconds
            while not words():
                if time.monotonic() > deadline:
                    raise TimeoutError(f'Resolve did not finish the transcription within {timeout_seconds} seconds.')
                self.sleep(2)
        return dict(media_id=media_id, words=words())

    # ---- render -----------------------------------------------------------------------------------------------
    def render(self, timeline_id, find, target_dir, file_name, timeout_seconds=3600, container='mp4', codec='H264'):
        """Render one timeline to <target_dir>/<file_name>.<container>. Never overwrites; waits for the job to end."""
        if not file_name or '/' in file_name or '\\' in file_name or '.' in file_name:
            raise ValueError('The file name must be plain, without extension or folders.')
        folder = Path(target_dir)
        if not folder.is_absolute() or not folder.is_dir():
            raise ValueError('The target folder must be an existing absolute folder.')
        target = folder / f'{file_name}.{container}'
        if target.exists():
            raise FileExistsError(f'{target} already exists.')
        _, p = self.project()
        timeline = find(p, timeline_id)
        p.SetCurrentTimeline(timeline)
        if not p.SetCurrentRenderFormatAndCodec(container, codec):
            raise RuntimeError(f'Resolve refused the format {container}/{codec}.')
        if not p.SetRenderSettings({'SelectAllFrames': True, 'TargetDir': str(folder), 'CustomName': file_name, 'ExportVideo': True, 'ExportAudio': True}):
            raise RuntimeError('Resolve refused the render settings.')
        job = p.AddRenderJob()
        if not job:
            raise RuntimeError('Resolve did not create the render job.')
        if not p.StartRendering([job], False):
            raise RuntimeError('Resolve did not start the render.')
        deadline = time.monotonic() + timeout_seconds
        status = {}
        while True:
            status = p.GetRenderJobStatus(job) or {}
            state = status.get('JobStatus')
            if state == 'Complete':
                break
            if state in ('Failed', 'Cancelled'):
                raise RuntimeError(f"The render ended as {state}: {status.get('Error') or 'no detail from Resolve'}.")
            if time.monotonic() > deadline:
                p.StopRendering()
                raise TimeoutError(f'The render did not finish within {timeout_seconds} seconds and was stopped.')
            self.sleep(1)
        if not target.is_file() or os.path.getsize(target) == 0:
            raise RuntimeError(f'Resolve reports the render complete but {target} is missing or empty.')
        return dict(file=str(target), size=os.path.getsize(target), job_id=job)

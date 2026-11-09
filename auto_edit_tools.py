"""MCP tools for automatic editing. Everything here only reads the project, except transcribing."""
import json
from pathlib import Path

from auto_edit import catalog, compare_cuts, plan_edit, reference_keeps, words_from_resolve, words_from_whisper


def _clips(folder):
    yield from folder.GetClipList() or []
    for child in folder.GetSubFolderList() or []:
        yield from _clips(child)


def find_media(p, media_id):
    matches = [c for c in _clips(p.GetMediaPool().GetRootFolder()) if c.GetMediaId() == media_id]
    if len(matches) != 1:
        raise ValueError('Media ID not found uniquely in current project.')
    return matches[0]


def media_facts(item):
    fps = float(item.GetClipProperty('FPS'))
    frames = int(item.GetClipProperty('Frames'))
    return dict(fps=fps, duration=frames / fps, start_timecode=item.GetClipProperty('Start TC') or '00:00:00:00')


def media_words(item, transcript_path=None):
    """Whisper's JSON when a path is given, otherwise the transcription stored by Resolve. None if there is neither."""
    facts = media_facts(item)
    if transcript_path:
        path = Path(transcript_path)
        if path.suffix.lower() != '.json':
            raise ValueError('A transcript must be a JSON file written by transcribe.py.')
        return words_from_whisper(json.loads(path.read_text(encoding='utf8'))), facts, 'whisper'
    words = words_from_resolve(item.GetTranscription() or {}, facts['fps'], facts['start_timecode'])
    return (words or None), facts, 'resolve' if words else 'none'


def load_sources(p, sources):
    """Maps each recipe source (file name or form question) to the media it uses in this project.

    `sources` is {key: {media_id, transcript_path?}}. A clip without transcription stays usable as long
    as its recipe does not need one: fixed introductions are never transcribed.
    """
    loaded, origins = {}, {}
    for key, entry in sources.items():
        words, facts, origin = media_words(find_media(p, entry['media_id']), entry.get('transcript_path'))
        loaded[key] = dict(duration=facts['duration'], fps=facts['fps'], words=words)
        origins[key] = origin
    return loaded, origins


def timeline_keeps(timeline, media_id, fps, track=1):
    """What the editor kept of this media on a finished timeline, in source seconds."""
    clips = []
    for clip in timeline.GetItemListInTrack('video', track) or []:
        media = clip.GetMediaPoolItem()
        if media is not None and media.GetMediaId() == media_id:
            clips.append(dict(start=clip.GetStart(), source_start_frame=clip.GetSourceStartFrame(), source_end_frame=clip.GetSourceEndFrame()))
    if not clips:
        raise ValueError(f'No clip of this media on video track {track} of that timeline.')
    return reference_keeps(clips, fps)


from montage_build import MontageBuilder


def register(mcp, lock, project, find, read, write):
    @mcp.tool(annotations=read)
    def auto_edit_catalog() -> dict:
        """List what a montage recipe can use: clip sources, per-clip treatments and finishing steps."""
        return catalog()

    @mcp.tool(annotations=read)
    def read_media_transcription(media_id: str) -> dict:
        """Summarize the transcription Resolve stored for a media, without changing anything."""
        with lock:
            _, p = project()
            item = find_media(p, media_id)
            facts = media_facts(item)
            transcription = item.GetTranscription() or {}
            words = words_from_resolve(transcription, facts['fps'], facts['start_timecode'])
            return dict(**facts, transcribed=bool(words), language=transcription.get('language'), word_count=len(words), first_words=' '.join(w['text'] for w in words[:30]))

    @mcp.tool(annotations=write)
    def transcribe_media_for_edit(media_id: str) -> dict:
        """Ask Resolve to transcribe a media. This stores transcription data in the open project: use a copy for tests."""
        with lock:
            _, p = project()
            return dict(started=bool(find_media(p, media_id).TranscribeAudio()))

    builder = MontageBuilder(project)

    @mcp.tool(annotations=write)
    def organize_montage_media(project_name: str, files: list[dict], folders: list[dict] | None = None, names: dict | None = None) -> dict:
        """Import media into <project_name>/<folder> bins of the Media Pool, creating missing bins and reusing existing ones.
        Each file: {path, role?, folder?}. A folder takes the roles/extensions it lists (defaults: Sources, Animations, Musique, Son,
        Montage fini, Autres). `folders` replaces the defaults; `names` renames some by id. Touches no timeline."""
        with lock:
            return builder.organize_media(project_name, files, folders, names)

    @mcp.tool(annotations=write)
    def build_montage_timeline(name: str, plan: dict, sources: dict) -> dict:
        """Create a NEW timeline with the pieces of a plan (`plan.sequence` from preview_auto_edit), back to back.
        `sources` maps each piece's source (form question or file name) to a media ID. Refuses an existing timeline name.
        Reports what the plan asks for that is not applied yet (overlays, fades, audio, markers, chapters)."""
        with lock:
            return builder.build_timeline(name, plan, sources)

    @mcp.tool(annotations=write)
    def render_montage(timeline_id: str, target_dir: str, file_name: str, timeout_seconds: int = 3600) -> dict:
        """Render a timeline to <target_dir>/<file_name>.mp4 (H.264) and wait for the end. Never overwrites a file."""
        with lock:
            return builder.render(timeline_id, find, target_dir, file_name, timeout_seconds)

    @mcp.tool(annotations=read)
    def preview_auto_edit(recipe: dict, sources: dict, answers: dict | None = None) -> dict:
        """Compute a montage plan. `recipe` lists clips in edit order; `sources` maps each file name or form question to {media_id, transcript_path?}. No timeline is created."""
        with lock:
            _, p = project()
            loaded, origins = load_sources(p, sources)
            return dict(transcripts=origins, plan=plan_edit(recipe, loaded, answers))

    @mcp.tool(annotations=read)
    def compare_auto_edit(recipe: dict, sources: dict, clip_id: str, reference_timeline_id: str, answers: dict | None = None, track: int = 1, window_seconds: float = 2.0) -> dict:
        """Compare one planned clip to the same video edited by hand on a timeline. Changes nothing."""
        with lock:
            _, p = project()
            loaded, origins = load_sources(p, sources)
            planned = plan_edit(recipe, loaded, answers)
            if clip_id not in planned['clips']:
                raise ValueError(f'Clip {clip_id} is not in this plan.')
            key = next(c['source'][c['source']['type']] for c in recipe['clips'] if c['id'] == clip_id)
            media = loaded[key]
            reference = timeline_keeps(find(p, reference_timeline_id), sources[key]['media_id'], media['fps'], track)
            keeps = planned['clips'][clip_id]['keeps']
            return dict(transcript=origins[key], comparison=compare_cuts(keeps, reference, media['words'] or (), window_seconds, media['fps']),
                        planned_duration=sum(b - a for a, b in keeps), reference_duration=sum(b - a for a, b in reference), needs_review=planned['needs_review'])

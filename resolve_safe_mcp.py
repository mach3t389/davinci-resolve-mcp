"""Local stdio Resolve bridge: inspection and markers on server-created copies only."""
import json
import os
import sys
import threading
import uuid
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

API = Path('C:/ProgramData/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting')
os.environ['RESOLVE_SCRIPT_LIB'] = 'C:/Program Files/Blackmagic Design/DaVinci Resolve/fusionscript.dll'
os.environ.setdefault('FUSION_PYTHON3_HOME', sys.base_prefix)
sys.path.insert(0, str(API / 'Modules'))
mcp = FastMCP('resolve_safe')
lock = threading.RLock()
copies = set()
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

def project():
    import DaVinciResolveScript as bmd
    resolve = bmd.scriptapp('Resolve')
    if resolve is None:
        raise RuntimeError('Open Resolve Studio and enable External scripting: Local.')
    p = resolve.GetProjectManager().GetCurrentProject()
    if p is None:
        raise RuntimeError('Open a project in Resolve.')
    return resolve, p

def timelines(p):
    return [p.GetTimelineByIndex(i) for i in range(1, p.GetTimelineCount() + 1)]

def find(p, timeline_id):
    matches = [t for t in timelines(p) if t.GetUniqueId() == timeline_id]
    if len(matches) != 1:
        raise ValueError('Timeline ID not found uniquely in current project.')
    return matches[0]

def identity(p, t):
    return (p.GetUniqueId(), t.GetUniqueId())

def summary(t):
    return dict(id=t.GetUniqueId(), name=t.GetName(), start_frame=t.GetStartFrame(),
                end_frame=t.GetEndFrame(), fps=t.GetSetting('timelineFrameRate'),
                width=t.GetSetting('timelineResolutionWidth'), height=t.GetSetting('timelineResolutionHeight'))

@mcp.tool(annotations=READ)
def inspect_project() -> dict:
    """Read version, project and timeline IDs; makes no changes."""
    with lock:
        r, p = project()
        return dict(version=r.GetVersionString(), project=p.GetName(),
                    project_id=p.GetUniqueId(), timelines=[summary(t) for t in timelines(p)])

@mcp.tool(annotations=READ)
def inspect_timeline(timeline_id: str) -> dict:
    """Read clips and markers. Frames for markers are relative to timeline start."""
    with lock:
        _, p = project()
        t = find(p, timeline_id)
        tracks = []
        for kind in ('video', 'audio'):
            for i in range(1, t.GetTrackCount(kind) + 1):
                clips = [dict(id=c.GetUniqueId(), name=c.GetName(), start=c.GetStart(), end=c.GetEnd(),
                              source_start_frame=c.GetSourceStartFrame(), source_end_frame=c.GetSourceEndFrame(),
                              duration=c.GetDuration()) for c in (t.GetItemListInTrack(kind, i) or [])]
                tracks.append(dict(kind=kind, index=i, clips=clips))
        return dict(**summary(t), tracks=tracks, markers=t.GetMarkers() or {})

@mcp.tool(annotations=WRITE)
def duplicate_for_marker_test(timeline_id: str) -> dict:
    """Create a fresh copy, preserving original; return the new ID for marker tests."""
    with lock:
        _, p = project()
        source = find(p, timeline_id)
        original = p.GetCurrentTimeline()
        name = 'MCP_TEST_' + uuid.uuid4().hex[:12]
        copied = source.DuplicateTimeline(name)
        if copied is None or copied is False:
            raise RuntimeError('Resolve did not create the copy.')
        if copied.GetUniqueId() == source.GetUniqueId():
            raise RuntimeError('Duplicate identity check failed; no marker authorized.')
        copies.add(identity(p, copied))
        restored = bool(p.SetCurrentTimeline(original)) if original else None
        return dict(**summary(copied), source_id=source.GetUniqueId(), original_selection_restored=restored)

@mcp.tool(annotations=WRITE)
def add_test_marker(timeline_id: str, frame_offset: int, name: str = 'MCP test', note: str = '') -> dict:
    """Add one Blue marker only to a copy created in this server session.
    frame_offset is an integer frame offset from timeline start, not a source timecode.
    Existing markers are never overwritten. No delete tool exists.
    """
    with lock:
        _, p = project()
        t = find(p, timeline_id)
        if identity(p, t) not in copies:
            raise ValueError('Writes allowed only on a copy created by this server session.')
        if not 0 <= frame_offset < t.GetEndFrame() - t.GetStartFrame():
            raise ValueError('Marker lies outside the timeline.')
        if frame_offset in (t.GetMarkers() or {}):
            raise ValueError('A marker already exists at this frame.')
        tag = 'codex-safe-test:' + uuid.uuid4().hex
        ok = t.AddMarker(frame_offset, 'Blue', name, note, 1, tag)
        after = t.GetMarkers() or {}
        verified = bool(ok and after.get(frame_offset, {}).get('customData') == tag)
        return dict(success=bool(ok), verified=verified, timeline_id=timeline_id,
                    frame_offset=frame_offset, marker=after.get(frame_offset))

from edit_bridge import EditBridge
edits = EditBridge(project, find, summary)

@mcp.tool(annotations=READ)
def preview_cut_plan(timeline_id: str, ranges: list[dict], mode: str = 'split_all', origin_offset: int = 0) -> dict:
    """Preview exact cuts without changing Resolve. Ranges have start/end elapsed
    timestamps (M:SS or H:MM:SS) or integer start_frame/end_frame, end exclusive.
    origin_offset locates the beginning of a source part within this timeline.
    split_all retains every passage; keep_with_gaps omits outside ranges without ripple.
    Supports raw, flat audio/video only; rejects unsupported/offline media and effects.
    Returns a session-local plan_id and all planned segments for human review.
    """
    with lock:
        return edits.preview(timeline_id, ranges, mode, origin_offset)

@mcp.tool(annotations=WRITE)
def create_cut_timeline(plan_id: str, confirmation: str = '') -> dict:
    """Create a NEW timeline from a reviewed preview; never edits/deletes the source.
    For keep_with_gaps, obtain human approval first, then use confirmation='CONFIRM <plan_id>'.
    A plan is single-use once import begins. Inspect partial results after any error,
    never retry blindly. Verifies frame positions, sources and original snapshot.
    Original selection is restored; the new timeline remains available for inspection.
    """
    with lock:
        return edits.create(plan_id, confirmation)

@mcp.tool(annotations=READ)
def verify_cut_timeline(timeline_id: str) -> dict:
    """Recheck the timing/media of a cut output created in this server session."""
    with lock:
        return edits.verify(timeline_id)

from sync_bridge import SyncBridge
sync = SyncBridge(project, find, summary, edits)

@mcp.tool(annotations=READ)
def inspect_sync_media() -> dict:
    """List media IDs, bins, audio availability and native sync capability. Read only."""
    with lock:
        return sync.inventory()

@mcp.tool(annotations=READ)
def preview_sync_pair(timeline_id: str, reference_audio_id: str, target_media_id: str, mode: str = 'waveform', search_start_frame: int = 0) -> dict:
    """Preview ONE target against a reference audio timeline item, without changes.
    Folder names identify angles; source timecode is used only when explicitly selected.
    """
    with lock:
        return sync.preview(timeline_id, reference_audio_id, target_media_id, mode, search_start_frame)

@mcp.tool(annotations=WRITE)
def create_sync_pair_copy(plan_id: str) -> dict:
    """Execute a single-use sync preview on a fresh timeline copy only. No deletes.
    Adds target on separate linked video/audio tracks, calls native AutoAlignClips,
    checks original and existing copy items. Native alignment can trim target boundaries
    at the copy's start; reports all source boundary changes. Always requires picture/sound review;
    Resolve provides no confidence score. Never retry after timeout; inspect status.
    """
    with lock:
        return sync.create(plan_id)

@mcp.tool(annotations=READ)
def inspect_sync_attempt(plan_id: str) -> dict:
    """Read session sync attempt state and partial copy ID before any retry."""
    with lock:
        return sync.status(plan_id)

from section_sync import SectionSync
section_sync = SectionSync(sync)

@mcp.tool(annotations=READ)
def preview_section_sync(timeline_id: str, target_media_id: str, reference_video_track: int = 1, reference_audio_track: int = 1) -> dict:
    """Plan separate waveform mappings for camera-one recording sections and manual edits.
    Source timeline stays authoritative. Requires matching FPS and original file media.
    """
    with lock:
        return section_sync.preview(timeline_id,target_media_id,reference_video_track,reference_audio_track)

@mcp.tool(annotations=WRITE)
def create_section_sync_copy(plan_id: str) -> dict:
    """Analyze each reference section on fresh copies; build bounded target segments on
    a NEW copy without moving/removing any existing clips. Single-use plan, preserve
    partial/analysis copies on error. Structural success requires subsequent acoustic review.
    """
    with lock:
        return section_sync.create(plan_id)

@mcp.tool(annotations=READ)
def inspect_section_sync_attempt(plan_id: str) -> dict:
    """Inspect session-local partial copies and section-sync status; never blind-retry."""
    with lock:
        return section_sync.status(plan_id)

@mcp.tool(annotations=WRITE)
def create_review_marker_copy(timeline_id: str, markers: list[dict], name: str = 'REVISION_CLIENT') -> dict:
    """Duplicate a timeline and add reviewed duration markers only. Each marker has
    integer frame/duration, color Green/Red/Yellow, name and note. Does not cut clips.
    Reports partial copies on failure; inspect returned ID before any repeat."""
    from review_markers import create_review_copy
    with lock:
        return create_review_copy(project, find, summary, edits, timeline_id, markers, name)

from motion_bridge import MotionBridge
motion = MotionBridge(project)

@mcp.tool(annotations=READ)
def list_motion_clips() -> dict:
    """List clips in the Media Pool "Motion" bin with media IDs and file paths. Read only."""
    with lock:
        return motion.clips()

@mcp.tool(annotations=WRITE)
def import_motion_clip(file_path: str) -> dict:
    """Import one Remotion render (<project>/out/*.mov, project marked by .motion-project) into the "Motion" bin.
    Does not touch any timeline; place the clip on the timeline manually."""
    with lock:
        return motion.import_clip(file_path)

@mcp.tool(annotations=WRITE)
def place_motion_clips(timeline_id: str, placements: list[dict], copy_name: str) -> dict:
    """Duplicate a timeline (the source is never edited) and place "Motion" bin clips on NEW video tracks of the copy.
    Each placement: {"media_id", "frame" (offset from timeline start), "track" (new track name)}.
    Existing tracks, clips and markers are left as they are and checked afterwards; overlapping clips on a track are
    rejected. Placement is overwrite on empty new tracks: nothing is rippled or moved. Original selection is restored."""
    with lock:
        _, p = project()
        return motion.place_on_copy(find(p, timeline_id), placements, copy_name)

@mcp.tool(annotations=WRITE)
def replace_motion_clip(media_id: str, file_path: str) -> dict:
    """Relink a "Motion" bin clip to a newer render; every timeline use updates.
    Both old and new files must be in a Motion project's out folder. No file is deleted:
    replace back with previous_file to undo."""
    with lock:
        return motion.replace_clip(media_id, file_path)

@mcp.tool(annotations=WRITE)
def probe_native_camera_split(timeline_id: str) -> dict:
    """Validate one native video split on a NEW copy; preserve all originals and audio.
    Never retry blindly: partial-copy identity is saved in work/camera-native-probe.json.
    """
    from camera_bridge import probe
    with lock:
        return probe(project, find, summary, timeline_id)

from camera_bridge import CameraEdits
cameras = CameraEdits(project, find, summary)

@mcp.tool(annotations=READ)
def preview_camera_edit(timeline_id: str, start_seconds: int = 128, rhythm: str = 'long') -> dict:
    """Plan long camera shots on video 1-3 from elapsed start through current Out.
    Camera 1 receives about two thirds, camera 3 about twice camera 2.
    Does not alter clips. Returns a single-use plan and unavoidable short-shot exceptions.
    """
    with lock:
        return cameras.preview(timeline_id, start_seconds, rhythm)

@mcp.tool(annotations=WRITE)
def create_camera_edit_copy(plan_id: str, copy_name: str) -> dict:
    """Apply a camera preview on a NEW native duplicate. Never deletes camera clips.
    Split video 1-3 natively and disable other angles; preserve source, audio and upper tracks.
    Single-use. After timeout/error inspect work/camera-edit-result.json; never retry blindly.
    """
    with lock:
        return cameras.create(plan_id, copy_name)

@mcp.tool(annotations=WRITE)
def verify_camera_edit_copy(open_copy: bool = True) -> dict:
    """Recheck the saved existing camera copy; never repeats cuts or creates another copy.
    Restore the source's output range on the copy, verify all footage, audio and upper tracks,
    open the copy if requested, and save the project. Source timeline is never modified.
    """
    from camera_verify import verify_existing
    with lock:
        return verify_existing(project, find, summary, open_copy)

@mcp.tool(annotations=READ)
def inspect_camera_edit_frames() -> dict:
    """Export three sample frames from the completed camera copy and source angles for picture review.
    Does not alter clips or grades. Restores timeline selection.
    """
    from camera_frames import export_review
    with lock:
        return export_review(project, find)

if __name__ == '__main__':
    if '--doctor' in sys.argv:
        try:
            print(json.dumps(inspect_project(), indent=2))
        except Exception as e:
            print(json.dumps(dict(connected=False, error=str(e))))
            sys.exit(1)
    else:
        mcp.run(transport='stdio')

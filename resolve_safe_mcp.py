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
                end_frame=t.GetEndFrame(), fps=t.GetSetting('timelineFrameRate'))

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
                clips = [dict(name=c.GetName(), start=c.GetStart(), end=c.GetEnd(),
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

if __name__ == '__main__':
    if '--doctor' in sys.argv:
        try:
            print(json.dumps(inspect_project(), indent=2))
        except Exception as e:
            print(json.dumps(dict(connected=False, error=str(e))))
            sys.exit(1)
    else:
        mcp.run(transport='stdio')

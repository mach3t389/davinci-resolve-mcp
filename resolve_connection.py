"""The connection to a running DaVinci Resolve Studio, shared by the MCP server and the montage command line.

Kept apart from resolve_safe_mcp.py so that the command line does not need the MCP package."""
import os
import sys
from pathlib import Path

API = Path('C:/ProgramData/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting')
os.environ['RESOLVE_SCRIPT_LIB'] = 'C:/Program Files/Blackmagic Design/DaVinci Resolve/fusionscript.dll'
os.environ.setdefault('FUSION_PYTHON3_HOME', sys.base_prefix)
sys.path.insert(0, str(API / 'Modules'))


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

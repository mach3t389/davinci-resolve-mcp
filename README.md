# DaVinci Resolve MCP

A local MCP server connecting Codex to DaVinci Resolve Studio for timeline inspection, markers, and controlled editing workflows.

## Current tools

- `inspect_project`: read the open project and timeline IDs.
- `inspect_timeline`: read clips, tracks, and markers.
- `duplicate_for_marker_test`: create a timeline copy and restore the previous timeline selection.
- `add_test_marker`: add a blue marker only to a copy created during the current server session. Existing markers cannot be overwritten.

There are no cut, delete, export, or arbitrary script execution tools. Restarting the server revokes write access to previously created copies. Marker positions are integer frame offsets from the timeline start, not source clip timestamps.

## Windows setup

1. Install DaVinci Resolve Studio and 64-bit Python. This bridge was tested with Python 3.12 and Resolve Studio 21.1.
2. In Resolve preferences, enable **External scripting: Local**. Keep Resolve open with a project loaded.
3. From this folder, create the Python environment and install dependencies:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The bridge uses the official scripting module supplied with Resolve at `C:/ProgramData/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/Modules` and `fusionscript.dll` from the default installation directory. Adjust these constants in the source for nonstandard installations. The bundled Developer/Scripting README is the reference for the installed Resolve API.

## Codex configuration

Copy `config.example.toml` to `.codex/config.toml` in the project and replace both example paths with the absolute paths on your computer. Open the folder as a trusted project in Codex and reload the MCP connection.

For a read-only connection, keep only `inspect_project` and `inspect_timeline` in `enabled_tools`.

A direct read-only connection check is available:

```powershell
.\.venv\Scripts\python.exe resolve_safe_mcp.py --doctor
```

## Safety and local files

Begin with inspection, then explicitly request a timeline copy before testing markers. The server checks project and timeline identities and rejects writes to original timelines. Copy creation and marker placement still modify the open Resolve project; review the results before saving. Tool annotations describe behavior but do not replace the checks implemented in the server.

Request human confirmation before introducing or using any destructive operation in future versions.

This repository excludes virtual environments, personal Codex configuration, credentials, logs, test results, and media/Resolve project files. No deployment workflow is configured. Never commit client footage or project data.

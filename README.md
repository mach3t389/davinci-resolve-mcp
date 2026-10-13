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

## Exact cuts and gaps

Three additional tools implement frame-exact cuts through Resolve's official OTIO export/import API:

- `preview_cut_plan(timeline_id, ranges, mode, origin_offset)` validates the source and returns a session-local preview token without changing the project.
- `create_cut_timeline(plan_id, confirmation)` imports a **new** timeline and verifies the result. The source timeline is never edited or deleted.
- `verify_cut_timeline(timeline_id)` checks a result again during the same server session.

A range is either `{"start":"0:16","end":"0:29","label":"Arrival"}` or `{"start_frame":384,"end_frame":696}`. Timestamps are elapsed M:SS or H:MM:SS (decimal seconds accepted), rounded half-up to the timeline frame rate. The end is exclusive: a cut at 0:29 excludes the frame starting at 0:29 from the selected range. `origin_offset` is the integer frame offset of a part's beginning relative to timeline start. Camera/part mapping must be checked by the human; ambiguous editorial notes must not be invented as exact ranges.

`split_all` cuts at every range boundary and retains all passages. `keep_with_gaps` retains the selections at their original positions and replaces other passages with gaps, without ripple. Obtain explicit human approval of the displayed ranges before using that mode, then pass `confirmation="CONFIRM <plan_id>"`. This token is an acknowledgement, not independent proof of human consent; the calling assistant must enforce approval.

Plans expire when the server restarts. Changes to the source invalidate a preview. Once import starts, the token cannot be retried, including after failure. Partial/new timelines are preserved for inspection, with no automatic deletion. Restore of the previous timeline selection is reported separately.

Only raw, flat video/audio timelines with online file media are supported. Nested/multicam compositions, transitions, speed effects, subtitles, and configured clip effects are rejected. OTIO cannot guarantee preservation of every Resolve grade, effect, automation or project feature: use an ungraded preparation timeline. Verification covers timeline/source timing, media paths, frame rate, start position, track structure, linked groups, and the source snapshot/export. Review picture and sound before using the result.

Development validation on Resolve Studio 21.1: two selection ranges produced five video and five linked audio segments, with frame positions and source in-points verified. Source unchanged. Gap placement is covered by automated tests; applying gap mode to client footage requires human approval and a live review.

Run offline tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py"
```

## Native audio alignment (experimental, one pair at a time)

- `inspect_sync_media`: read clip IDs, camera bins, audio channels and file availability.
- `preview_sync_pair`: select a reference **audio timeline item ID** and one target media ID. Choose waveform or explicitly verified common timecode. No project writes.
- `create_sync_pair_copy`: duplicate the source, append one linked target video/audio pair on new tracks, and call the installed native `AutoAlignClips` API. Never modifies the source timeline or deletes clips/media. Restores original timeline selection.
- `inspect_sync_attempt`: read the current session's attempt state and partial copy ID. Plans are single-use once writes begin. After a timeout, inspect the project/status rather than resubmitting. Restarted sessions cannot recover old plan tokens; use timeline inspection and the returned copy ID.

`inspect_timeline` includes item IDs and source frame ranges for choosing references. Folder labels identify angles; file timecodes are not assumed to be synchronized. This first implementation aligns one pair, not an entire bin or five cameras automatically.

Resolve provides a boolean result, not a correlation confidence score. Native alignment can trim a target at timeline boundaries. Reports distinguish `technical_checks_passed`, `target_duration_preserved`, source-boundary changes, and `alignment_verified` (always false until external/human review). `ready_for_batch` is false. Validate picture/sound and drift before adding further angles. Pair timing/source mismatch is detected and reported; no automatic retry or cleanup occurs.

Live test: the original preparation timeline was duplicated. Selecting reference audio plus target audio alone failed to move target video. Selecting reference audio plus **both** target video/audio aligned the pair consistently. Resolve trimmed 1284 frames from the target beginning, which lies before the reference timeline start. Existing copied items and original source snapshot remained unchanged. Test copies remain available; no clip/file/timeline was deleted. This verifies structural behavior, not acoustic correctness or drift over forty minutes.

For long waveform analysis, the example configuration allows a 1200-second tool timeout. Keep the client session open for preview, execution and status. Always review the first pair before applying to further recordings.

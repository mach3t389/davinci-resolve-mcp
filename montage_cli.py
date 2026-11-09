"""One request in, one answer out, for the montage worker of Rushflow (it starts this program, it is not an MCP server).

stdin: {"command": "import" | "transcribe" | "build" | "render", ...arguments}
stdout: one JSON line, {"ok": true, "result": {...}} or {"ok": false, "error": "...", "kind": "ValueError"}.
The commands are the ones of the MCP tools (organize_montage_media, build_montage_timeline, render_montage)
plus `transcribe`; the work is done by MontageBuilder, nothing is duplicated."""
import json
import sys

COMMANDS = {
    'import': lambda b, r, find: b.organize_media(r['project_name'], r['files'], r.get('folders'), r.get('names')),
    'transcribe': lambda b, r, find: b.transcribe(r['media_id'], r.get('timeout_seconds', 1800)),
    'build': lambda b, r, find: b.build_timeline(r['name'], r['plan'], r['sources']),
    'render': lambda b, r, find: b.render(r['timeline_id'], find, r['target_dir'], r['file_name'], r.get('timeout_seconds', 3600)),
}


def handle(request, builder, find):
    command = COMMANDS.get(request.get('command')) if isinstance(request, dict) else None
    if command is None:
        raise ValueError(f"Unknown command: {request.get('command') if isinstance(request, dict) else request!r}")
    return command(builder, request, find)


def main(stdin=sys.stdin, stdout=sys.stdout):
    try:
        request = json.loads(stdin.read())
        # Imported here so that tests of `handle` do not need Resolve or the MCP package.
        from montage_build import MontageBuilder
        from resolve_connection import find, project
        answer = dict(ok=True, result=handle(request, MontageBuilder(project), find))
    except Exception as error:  # the worker reads the kind and message, nothing else
        answer = dict(ok=False, error=str(error), kind=type(error).__name__)
    stdout.write(json.dumps(answer) + '\n')
    return 0 if answer['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())

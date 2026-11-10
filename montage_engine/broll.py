"""B-roll: mini-scenes and unused shots (doc 25, step 8). Pure helpers; nothing here deletes a clip.

Alexis's way of working: select the best take of each shot, group the shots of one small scene side by side so they
overlap and assemble, and NEVER delete a shot without his authorisation: unused shots are parked after the end of
the video and the system recommends good ones that were not used.
"""
import re


def group_scenes(items, max_gap_seconds=90, key=None):
    """items: [{'file', 'time' (shooting time in seconds), 'card'?}] -> list of scenes (lists), shots of one scene
    being less than max_gap_seconds apart on the same card/camera. `key` can add a grouping key (e.g. folder)."""
    scenes = []
    by = {}
    for it in sorted(items, key=lambda x: (key(x) if key else '', x.get('card', ''), x['time'])):
        k = (key(it) if key else '', it.get('card', ''))
        cur = by.get(k)
        if cur and it['time'] - cur[-1]['time'] <= max_gap_seconds:
            cur.append(it)
        else:
            cur = [it]
            by[k] = cur
            scenes.append(cur)
    return sorted(scenes, key=lambda s: s[0]['time'])


_CARD = re.compile(r'(?P<cam>[A-Z]\d{3})[_-]?(?P<stamp>\d{6,8})?')


def camera_card(name):
    """'A029_03011557_C017.mov' -> 'A029' (the card/roll), used to keep takes of one roll together."""
    m = _CARD.search(name or '')
    return m.group('cam') if m else ''


def unused(all_files, used_files):
    """Shots of the bin that the timeline does not use, in the bin's order."""
    used = set(used_files)
    return [f for f in all_files if f not in used]


def plan_parking(unused_files, end_frame, durations, gap=0):
    """Where to park unused shots: one after the other on the b-roll track starting at end_frame + 24 s of margin.
    durations: {file: frames}. -> [(file, start_frame, duration)]. Nothing is removed or trimmed."""
    start, out = end_frame + 24 * 24, []
    for f in unused_files:
        d = durations.get(f, 0)
        if d > 0:
            out.append((f, start, d))
            start += d + gap
    return out

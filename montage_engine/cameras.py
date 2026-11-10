"""Camera planning from directives (doc 25, step 6). Pure: it proposes which camera to show, it never edits.

Input: the candidate switch points (existing cuts of the top camera track), the visible jump cuts (where the source
jumps, so switching hides them), the overlay fade zones (never switch inside one) and the directives.
Output: runs [(start, end, 'main'|'secondary')] and the jump cuts that stay visible. Applying a plan (enable or
disable the top camera clips of a COPY) is the adapter `apply_runs`.
"""


def plan_runs(start, end, candidates, jump_cuts, fades, values, fps=24, start_with='main'):
    cam = values['cameras']
    main_min = int(round((cam.get('main_min_seconds') or 6) * fps))
    sec_min, sec_max = [int(round(s * fps)) for s in (cam.get('secondary_seconds') or (3, 10))]
    min_shot = int(round((cam.get('min_shot_seconds') or 0) * fps))
    sec_min = max(sec_min, min_shot)
    main_min = max(main_min, min_shot)

    def in_fade(f):
        return any(a < f < b for a, b in fades)

    # A main shot is as long as needed to reach the series' share of the main camera given the secondary length.
    share = cam.get('main_share') or 0.67
    main_target = max(main_min, int(round(sec_min * share / max(1 - share, 0.05))))
    cands = sorted(c for c in set(candidates) if start < c < end and not in_fade(c))
    jumps = set(jump_cuts)
    runs, cur, last = [], start_with, start
    for c in cands:
        length = c - last
        if cur == 'main':
            take = length >= main_target or (c in jumps and length >= main_min)
        else:
            take = length >= sec_min
        if take and end - c >= (sec_min if cur == 'main' else main_min):
            runs.append((last, c, cur))
            cur, last = ('secondary' if cur == 'main' else 'main'), c
    runs.append((last, end, cur))
    switches = {r[1] for r in runs[:-1]}
    return dict(runs=runs, visible_jump_cuts=sorted(j for j in jumps if j not in switches and start < j < end),
                share_main=sum(b - a for a, b, c in runs if c == 'main') / max(end - start, 1))


def apply_runs(top_clips, runs, main_is_top=True):
    """Enable/disable the clips of the TOP camera track on a copy so that `runs` is what the viewer sees.
    top_clips: [(clip object with GetStart/SetClipEnabled, start_frame_relative)]. When the top track holds the main
    camera, main = enabled; otherwise main = disabled. Returns the number of clips changed."""
    changed = 0
    for clip, rel_start in top_clips:
        want = next((c for a, b, c in runs if a <= rel_start < b), None)
        if want is None:
            continue
        enabled = (want == 'main') == main_is_top
        if bool(clip.GetClipEnabled()) != enabled:
            clip.SetClipEnabled(enabled)
            changed += 1
    return changed

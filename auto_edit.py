"""Automatic edit planning: a recipe listing clips in edit order, turned into a reviewable plan.

Pure code with no Resolve dependency, so the rules can be tested and compared to real edits offline.
What you see top to bottom in a recipe is what plays left to right in the edit. Each clip is a fixed file
or a video received in a form, and carries its own treatments. Adding a capability means adding one
treatment here, without changing existing recipes or the recipe panel.
Times are seconds in each clip's own source until `final` values are computed.
"""
import json
import re
import statistics
import sys

RECIPE_VERSION = 2
TREATMENTS = {}
FINISHES = {}
OK, CHECK, DECIDE = 'green', 'orange', 'red'
SEVERITY = {OK: 0, CHECK: 1, DECIDE: 2}


class RecipeError(ValueError):
    """The recipe cannot be applied as written: stop instead of guessing."""


def _register(registry, kind, version, label, params):
    def add(apply):
        registry[kind] = dict(version=version, label=label, params=params, apply=apply)
        return apply
    return add


def treatment(kind, version, label, params):
    """Something done to one clip. It can be assigned to one clip, several, or all."""
    return _register(TREATMENTS, kind, version, label, params)


def finish(kind, version, label, params):
    """Something done to the whole edit."""
    return _register(FINISHES, kind, version, label, params)


def number(default=None, minimum=None, maximum=None, label=''):
    return dict(type='number', default=default, minimum=minimum, maximum=maximum, label=label)


def choice(options, default, label=''):
    return dict(type='choice', options=options, default=default, label=label)


def flag(default, label=''):
    return dict(type='boolean', default=default, label=label)


def text(default=None, label=''):
    return dict(type='text', default=default, label=label)


def catalog():
    """What the engine can do. The recipe panel is built from this, never hard-coded."""
    def published(registry):
        return [dict(type=kind, version=b['version'], label=b['label'], params=b['params']) for kind, b in registry.items()]
    return dict(recipe_version=RECIPE_VERSION, sources=['file', 'field'], treatments=published(TREATMENTS), finishes=published(FINISHES))


def _check_param(kind, name, spec, value):
    if value is None:
        return
    if spec['type'] == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RecipeError(f'{kind}.{name} must be a number')
        if spec.get('minimum') is not None and value < spec['minimum']:
            raise RecipeError(f'{kind}.{name} is below {spec["minimum"]}')
        if spec.get('maximum') is not None and value > spec['maximum']:
            raise RecipeError(f'{kind}.{name} is above {spec["maximum"]}')
    elif spec['type'] == 'boolean' and not isinstance(value, bool):
        raise RecipeError(f'{kind}.{name} must be true or false')
    elif spec['type'] == 'choice' and value not in spec['options']:
        raise RecipeError(f'{kind}.{name} must be one of {spec["options"]}')
    elif spec['type'] == 'text' and not isinstance(value, str):
        raise RecipeError(f'{kind}.{name} must be text')


def _resolve_entry(registry, entry, where, shared):
    kind = entry.get('type') if isinstance(entry, dict) else None
    if kind not in registry:
        raise RecipeError(f'unknown step in {where}: {kind}')
    spec = registry[kind]
    given = entry.get('params', {})
    for name in given:
        if name not in spec['params']:
            raise RecipeError(f'unknown setting {kind}.{name}')
    # A clip's own value wins, then the recipe's shared setting, then the default.
    common = shared.get(kind, {})
    params = {name: given.get(name, common.get(name, s.get('default'))) for name, s in spec['params'].items()}
    for name, s in spec['params'].items():
        _check_param(kind, name, s, params[name])
    return dict(type=kind, params=params, enabled=entry.get('enabled', True))


def validate_recipe(recipe):
    """Refuses anything unknown or incomplete, and returns the recipe with its defaults applied."""
    if not isinstance(recipe, dict) or recipe.get('version') != RECIPE_VERSION:
        raise RecipeError(f'recipe version must be {RECIPE_VERSION}')
    clips = recipe.get('clips')
    if not isinstance(clips, list) or not clips:
        raise RecipeError('a recipe needs at least one clip')
    shared = recipe.get('settings', {})
    if not isinstance(shared, dict):
        raise RecipeError('recipe settings must be an object')
    for kind, values in shared.items():
        if kind not in TREATMENTS:
            raise RecipeError(f'unknown shared setting: {kind}')
        for name in values:
            if name not in TREATMENTS[kind]['params']:
                raise RecipeError(f'unknown setting {kind}.{name}')
    resolved, seen = [], set()
    for position, clip in enumerate(clips, 1):
        if not isinstance(clip, dict) or not clip.get('id'):
            raise RecipeError(f'clip {position} needs an id')
        if clip['id'] in seen:
            raise RecipeError(f'duplicate clip id: {clip["id"]}')
        seen.add(clip['id'])
        source = clip.get('source')
        if not isinstance(source, dict) or source.get('type') not in ('file', 'field') or not source.get(source.get('type')):
            raise RecipeError(f'clip {clip["id"]} needs a file or a form question as its source')
        when = clip.get('when')
        if when is not None and (not isinstance(when, dict) or set(when) != {'field', 'equals'}):
            raise RecipeError(f'clip {clip["id"]}: a condition needs exactly a field and a value')
        resolved.append(dict(id=clip['id'], label=clip.get('label') or clip['id'], key=source[source['type']], source_type=source['type'],
                             optional=bool(clip.get('optional')), when=when, enabled=clip.get('enabled', True),
                             treatments=[_resolve_entry(TREATMENTS, entry, f'clip {clip["id"]}', shared) for entry in clip.get('treatments', [])]))
    finishes = [_resolve_entry(FINISHES, entry, 'the finishing steps', {}) for entry in recipe.get('finish', [])]
    return dict(clips=resolved, finish=finishes)


class ClipPlan:
    """The cuts of one clip, in that clip's own time."""

    def __init__(self, clip, media, answers):
        self.clip = clip
        self.words = sorted(media.get('words') or [], key=lambda w: w['start'])
        self.has_transcript = media.get('words') is not None
        self.duration = media['duration']
        self.fps = media.get('fps', 24)
        self.answers = answers
        self.keeps = [[0.0, self.duration]]
        self.cuts = []
        self.placed = []
        self.flags = []

    def snap(self, seconds):
        return round(seconds * self.fps) / self.fps

    def words_around(self, seconds):
        before = [w['text'] for w in self.words if w['end'] <= seconds][-3:]
        after = [w['text'] for w in self.words if w['start'] >= seconds][:3]
        return ' '.join(before), ' '.join(after)

    def require_transcript(self, step):
        if not self.has_transcript:
            raise RecipeError(f'{self.clip["label"]}: {step} needs a transcription of this clip')

    def gaps(self):
        """Silent stretches between words, including before the first and after the last."""
        edges = [0.0]
        result = []
        for word in self.words:
            if word['start'] > edges[-1]:
                result.append((edges[-1], word['start']))
            edges.append(max(edges[-1], word['end']))
        if self.duration > edges[-1]:
            result.append((edges[-1], self.duration))
        return result

    def remove(self, start, end, kind, severity=OK, reason=''):
        start, end = self.snap(start), self.snap(end)
        if end <= start:
            return
        kept = []
        for a, b in self.keeps:
            if end <= a or start >= b:
                kept.append([a, b])
                continue
            if start > a:
                kept.append([a, start])
            if end < b:
                kept.append([end, b])
        self.keeps = kept
        before, after = self.words_around(start)[0], self.words_around(end)[1]
        self.cuts.append(dict(clip=self.clip['id'], kind=kind, start=start, end=end, severity=severity, reason=reason, before=before, after=after))

    def low_confidence_near(self, seconds):
        return any(w.get('p') is not None and w['p'] < 0.5 and abs(w['start'] - seconds) < 1.0 for w in self.words)

    def is_kept(self, seconds):
        return any(a <= seconds < b for a, b in self.keeps)

    def kept_length(self):
        """What the clip lasts once its cuts are made, without the clips inserted into it."""
        return sum(b - a for a, b in self.keeps)

    def source_at(self, offset):
        """The source moment `offset` seconds into what this clip keeps, or None past its end."""
        done = 0.0
        for a, b in self.keeps:
            if offset < done + (b - a) - 1e-9:
                return a + offset - done
            done += b - a
        return None

    def find_phrase(self, phrase):
        """Where the first occurrence of a spoken word or phrase starts, ignoring case and punctuation."""
        def clean(value):
            return re.sub(r'[^\w]', '', value.lower())
        tokens = [clean(t) for t in phrase.split() if clean(t)]
        spoken = [clean(w['text']) for w in self.words]
        for i in range(len(spoken) - len(tokens) + 1):
            if tokens and spoken[i:i + len(tokens)] == tokens:
                return self.words[i]['start']
        return None

    def anchor_to_source(self, item):
        """Turns a placement's anchor into a moment of this clip's source. Returns (time, problem)."""
        anchor = item['anchor']
        if anchor in ('source_seconds', 'field'):
            return float(item['value']), None
        if anchor == 'phrase':
            found = self.find_phrase(item['value'])
            return (found, None) if found is not None else (None, 'phrase_not_found')
        length = self.kept_length()
        offset = {'from_start': item['value'], 'percent': length * item['value'] / 100, 'before_end': length - item['value']}[anchor]
        at = self.source_at(offset) if offset >= 0 else None
        return (at, None) if at is not None else (None, 'anchor_outside_clip')


def _answer(plan, field, label):
    if field not in plan.answers:
        raise RecipeError(f'{plan.clip["label"]}: {label}: the form has no answer for {field}')
    return plan.answers[field]


def _range_answer(plan, field):
    value = _answer(plan, field, 'trim')
    if not isinstance(value, dict):
        raise RecipeError(f'{plan.clip["label"]}: trim: {field} is not a passage')
    start, end = value.get('startSeconds', value.get('start')), value.get('endSeconds', value.get('end'))
    if start is None or end is None:
        raise RecipeError(f'{plan.clip["label"]}: trim: {field} is incomplete')
    return start, end


def _nearest_gap(plan, target, search):
    best = None
    for start, end in plan.gaps():
        point = min(max(target, start), end)
        if abs(point - target) <= search and (best is None or abs(point - target) < abs(best[0] - target)):
            best = (point, start, end)
    return best


@treatment('trim', 1, 'Couper selon les temps donnés', dict(
    in_seconds=number(None, 0, label='Début'), out_seconds=number(None, 0, label='Fin'),
    range_field=text(label='Question du formulaire pour le passage'),
    in_field=text(label='Question du formulaire pour le début'), out_field=text(label='Question du formulaire pour la fin'),
    precision=choice(['exact', 'approximate'], 'exact', 'Précision des temps'),
    search_seconds=number(2.0, 0, 30, 'Déplacement maximal'), margin_seconds=number(0.08, 0, 2, 'Marge autour de la parole')))
def _trim(plan, p):
    """`exact` (default): the given moments are trusted. The cut is made at that moment, to the frame, with no transcription and no review.
    `approximate`: the given moments are only a zone. Speech around them is analysed, up to `search_seconds` either way,
    to find the best place to cut, and a cut that moved far or found no silence is marked for review."""
    label = plan.clip['label']

    def as_time(value, name):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RecipeError(f'{label}: the trim {name} is not a time')
        return float(value)

    def inside(value, name):
        if 0 <= value <= plan.duration:
            return True
        plan.flags.append(dict(severity=DECIDE, reason='time_outside_clip', clip=plan.clip['id'], detail=f'{name}: {value}'))
        return False

    def boundary(target, is_start):
        if p['precision'] == 'exact':
            return target, OK, 'exact_time'
        plan.require_transcript('finding the best moment to cut')
        found = _nearest_gap(plan, target, p['search_seconds'])
        if not found:
            return target, DECIDE, 'no_silence_near_requested_time'
        _, gap_start, gap_end = found
        # Stay in the silence, next to the speech that is kept.
        point = max(gap_start, gap_end - p['margin_seconds']) if is_start else min(gap_end, gap_start + p['margin_seconds'])
        if abs(point - target) > p['search_seconds']:
            point = min(max(target, gap_start), gap_end)
        moved = abs(point - target)
        severity = OK if moved <= p['search_seconds'] / 2 else CHECK
        return point, severity, 'moved_to_silence' if moved else 'on_silence'

    start, end = p['in_seconds'], p['out_seconds']
    if p['range_field']:
        start, end = _range_answer(plan, p['range_field'])
    if p['in_field']:
        start = _answer(plan, p['in_field'], 'trim start')
    if p['out_field']:
        end = _answer(plan, p['out_field'], 'trim end')
    start = None if start is None else as_time(start, 'start')
    end = None if end is None else as_time(end, 'end')
    if start is not None and not inside(start, 'start'):
        start = None
    if end is not None and not inside(end, 'end'):
        end = None
    if start is not None and end is not None and end <= start:
        raise RecipeError(f'{label}: the requested end is before the requested start')
    if start is not None:
        point, severity, reason = boundary(start, True)
        plan.remove(0.0, point, 'trim_start', severity, reason)
    if end is not None:
        point, severity, reason = boundary(end, False)
        plan.remove(point, plan.duration, 'trim_end', severity, reason)


@treatment('silence', 1, 'Couper les temps morts', dict(
    min_pause_seconds=number(0.7, 0.1, 30, 'Pause minimale'), keep_pause_seconds=number(0.25, 0, 10, 'Durée gardée'),
    min_segment_seconds=number(0.4, 0, 10, 'Passage minimal entre deux coupes')))
def _silence(plan, p):
    plan.require_transcript('cutting silences')
    keep = p['keep_pause_seconds']
    for gap_start, gap_end in plan.gaps():
        if gap_start <= 0 or gap_end >= plan.duration or gap_end - gap_start < p['min_pause_seconds']:
            continue
        start, end = gap_start + keep / 2, gap_end - keep / 2
        inside = [k for k in plan.keeps if k[0] <= start and end <= k[1]]
        if not inside or end <= start:
            continue
        if start - inside[0][0] < p['min_segment_seconds'] or inside[0][1] - end < p['min_segment_seconds']:
            continue
        doubtful = plan.low_confidence_near(gap_start) or plan.low_confidence_near(gap_end)
        plan.remove(start, end, 'silence', CHECK if doubtful else OK, 'uncertain_words_nearby' if doubtful else '')


ANCHORS = ['from_start', 'percent', 'before_end', 'source_seconds', 'field', 'phrase']


@treatment('place_clip', 1, 'Placer un clip ou une animation dans ce clip', dict(
    file=text(label='Fichier'), anchor=choice(ANCHORS, 'from_start', 'Moment'),
    seconds=number(None, 0, label='Secondes'), percent=number(None, 0, 100, 'Pourcentage du clip'),
    field=text(label='Question du formulaire pour le temps'), phrase=text(label='Mot ou phrase prononcée'),
    mode=choice(['insert', 'overlay'], 'overlay', 'Insérer ou superposer'), keep_clip_audio=flag(True, 'Garder le son du clip'),
    chapter_title=text(label='Titre du chapitre (facultatif)')))
def _place_clip(plan, p):
    """Anchors: seconds after the clip's start, a percentage of the clip, seconds before its end (all measured
    after the cuts), a time in the clip's own video from the form or the recipe, or a spoken phrase."""
    label = plan.clip['label']
    if not p['file']:
        raise RecipeError(f'{label}: a file is required to place a clip')
    anchor = p['anchor']
    if anchor in ('from_start', 'before_end', 'source_seconds'):
        value = p['seconds']
        if value is None:
            raise RecipeError(f'{label}: a number of seconds is required for this moment')
    elif anchor == 'percent':
        value = p['percent']
        if value is None:
            raise RecipeError(f'{label}: a percentage is required for this moment')
    elif anchor == 'field':
        if not p['field']:
            raise RecipeError(f'{label}: a form question is required for this moment')
        value = _answer(plan, p['field'], 'clip time')
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RecipeError(f'{label}: the answer for {p["field"]} is not a time')
    else:
        if not p['phrase'] or not p['phrase'].strip():
            raise RecipeError(f'{label}: a word or phrase is required for this moment')
        plan.require_transcript('finding a spoken phrase')
        value = p['phrase']
    plan.placed.append(dict(file=p['file'], anchor=anchor, value=value, mode=p['mode'], keep_clip_audio=p['keep_clip_audio'], chapter_title=p['chapter_title']))


@finish('fade', 1, 'Fondu aux coupes', dict(video_seconds=number(0, 0, 10, 'Fondu vidéo'), audio_seconds=number(0.03, 0, 5, 'Fondu sonore')))
def _fade(result, p):
    result['fades'].append(dict(video_seconds=p['video_seconds'], audio_seconds=p['audio_seconds']))


@finish('normalize', 1, 'Normaliser le son', dict(target_lufs=number(-16, -40, 0, 'Niveau visé')))
def _normalize(result, p):
    result['audio'].append(dict(operation='normalize', target_lufs=p['target_lufs']))


@finish('instruction', 1, "Instruction à l'agent", dict(body=text(label='Texte')))
def _instruction(result, p):
    result['flags'].append(dict(severity=DECIDE, reason='agent_instruction_needs_review', detail=p['body'] or ''))


def plan_edit(recipe, sources, answers=None):
    """Applies the recipe. Nothing is changed in Resolve: the result is only a plan.

    `sources` maps a file name or form question to {duration, fps, words}. A form question with no
    video is absent: its clip is skipped when optional, and stops the plan otherwise.
    """
    resolved = validate_recipe(recipe)
    answers = answers or {}
    sequence, cuts, overlays, flags, skipped, chapters, clip_results = [], [], [], [], [], [], {}
    elapsed = 0.0
    rates = set()

    def media_for(key, label):
        media = sources.get(key)
        if media is None:
            raise RecipeError(f'{label}: no video for {key}')
        if not media.get('duration') or media['duration'] <= 0:
            raise RecipeError(f'{label}: {key} has no duration')
        return media

    for clip in resolved['clips']:
        if not clip['enabled']:
            continue
        if clip['when'] and answers.get(clip['when']['field']) != clip['when']['equals']:
            skipped.append(dict(clip=clip['id'], reason='condition_not_met'))
            continue
        if clip['key'] not in sources and clip['optional']:
            skipped.append(dict(clip=clip['id'], reason='not_provided'))
            continue
        plan = ClipPlan(clip, media_for(clip['key'], clip['label']), answers)
        rates.add(plan.fps)
        for entry in clip['treatments']:
            if entry['enabled']:
                TREATMENTS[entry['type']]['apply'](plan, entry['params'])
        if not plan.keeps:
            raise RecipeError(f'{clip["label"]}: the recipe removes the whole clip')

        flags.extend(plan.flags)
        inserts, clip_start, placements = {}, elapsed, []
        for item in plan.placed:
            at, problem = plan.anchor_to_source(item)
            if problem is None and not plan.is_kept(at):
                problem = 'clip_time_in_removed_passage'
            if problem:
                flags.append(dict(severity=DECIDE, reason=problem, clip=clip['id'], detail=item['file'], anchor=item['anchor']))
                continue
            item = dict(item, at=at)
            placements.append(item)
            if item['mode'] == 'insert':
                inserts[at] = item

        positions = []
        for a, b in plan.keeps:
            cursor = a
            for point in sorted(t for t in inserts if a <= t < b):
                if point > cursor:
                    sequence.append(dict(kind='source', clip=clip['id'], label=clip['label'], source=clip['key'], start=cursor, end=point, final_start=round(elapsed, 6)))
                    positions.append((cursor, point, elapsed))
                    elapsed += point - cursor
                inserted = inserts[point]
                length = media_for(inserted['file'], clip['label'])['duration']
                sequence.append(dict(kind='placed', clip=clip['id'], source=inserted['file'], start=0.0, end=length, keep_clip_audio=inserted['keep_clip_audio'], final_start=round(elapsed, 6)))
                if inserted['chapter_title']:
                    chapters.append(dict(time=round(elapsed, 6), title=inserted['chapter_title']))
                elapsed += length
                cursor = point
            if b > cursor:
                sequence.append(dict(kind='source', clip=clip['id'], label=clip['label'], source=clip['key'], start=cursor, end=b, final_start=round(elapsed, 6)))
                positions.append((cursor, b, elapsed))
                elapsed += b - cursor

        def final_time(seconds, table=tuple(positions)):
            for a, b, offset in table:
                if a <= seconds <= b:
                    return round(offset + seconds - a, 6)
            return None

        for item in placements:
            if item['mode'] != 'overlay':
                continue
            length = media_for(item['file'], clip['label'])['duration']
            start = final_time(item['at'])
            overlays.append(dict(clip=clip['id'], file=item['file'], duration=length, source_time=item['at'], final_start=start, chapter_title=item['chapter_title']))
            if start + length > elapsed + 1e-6:
                flags.append(dict(severity=CHECK, reason='overlay_extends_past_clip', clip=clip['id'], detail=item['file'], final_start=start))
        for cut in plan.cuts:
            moment = final_time(cut['end'])
            if moment is None:
                moment = final_time(cut['start'])
            if moment is None:
                moment = round(clip_start if cut['kind'] == 'trim_start' else elapsed, 6)
            cuts.append(dict(cut, final_time=moment))
        clip_results[clip['id']] = dict(keeps=plan.keeps, final_start=round(clip_start, 6), final_end=round(elapsed, 6))

    # One overlay lane: a second animation starting while another still plays is not placed.
    lane, last_end = [], None
    for overlay in sorted(overlays, key=lambda o: o['final_start']):
        if last_end is not None and overlay['final_start'] < last_end - 1e-6:
            flags.append(dict(severity=DECIDE, reason='overlay_overlap', clip=overlay['clip'], detail=overlay['file'], final_start=overlay['final_start']))
            continue
        lane.append(overlay)
        last_end = overlay['final_start'] + overlay['duration']
    overlays = lane
    chapters.extend(dict(time=o['final_start'], title=o['chapter_title']) for o in overlays if o['chapter_title'])
    if not sequence:
        raise RecipeError('the recipe leaves nothing to edit')
    if len(rates) > 1:
        flags.append(dict(severity=CHECK, reason='clips_have_different_frame_rates', detail=sorted(rates)))
    markers = [dict(time=c['final_time'], color=c['severity'], clip=c['clip'], kind=c['kind'], note=f"{c['before']} | {c['after']}".strip(' |'), reason=c['reason']) for c in cuts]
    markers += [dict(time=o['final_start'], color='blue', clip=o['clip'], kind='overlay', note=o['file'], reason='') for o in overlays]
    result = dict(recipe_version=RECIPE_VERSION, final_duration=round(elapsed, 6), sequence=sequence, clips=clip_results, skipped=skipped, overlays=overlays, cuts=cuts,
                  markers=sorted(markers, key=lambda m: m['time']), chapters=sorted(chapters, key=lambda c: c['time']), fades=[], audio=[], flags=flags)
    for entry in resolved['finish']:
        if entry['enabled']:
            FINISHES[entry['type']]['apply'](result, entry['params'])
    result['needs_review'] = max([SEVERITY[c['severity']] for c in cuts] + [SEVERITY[f['severity']] for f in result['flags']] + [0]) > 0
    return result


# ---- Transcripts ----

def timecode_seconds(value, fps):
    match = re.fullmatch(r'(\d+):(\d+):(\d+)[:;](\d+)', value.strip())
    if not match:
        raise ValueError(f'invalid timecode: {value}')
    hours, minutes, seconds, frames = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + frames / fps


def words_from_resolve(transcription, fps, start_timecode='00:00:00:00'):
    """Resolve 21.1 GetTranscription. '(...)' marks silence, which is deduced from the gaps instead."""
    origin = timecode_seconds(start_timecode, fps)
    words = []
    for segment in transcription.get('segments', []):
        for word in segment.get('words', []):
            if word.get('text', '').strip() in ('', '(...)'):
                continue
            words.append(dict(start=timecode_seconds(word['start'], fps) - origin, end=timecode_seconds(word['end'], fps) - origin, text=word['text'].strip()))
    return words


def words_from_whisper(transcript):
    """The JSON written by Motion's scripts/transcribe.py."""
    return [dict(start=w['start'], end=w['end'], text=w['text'], p=w.get('p')) for s in transcript.get('segments', []) for w in s.get('words', [])]


# ---- Comparison with a real edit ----

def reference_keeps(clips, fps):
    """Kept source passages of a finished timeline, in frames from inspect_timeline."""
    ordered = sorted(clips, key=lambda c: c['start'])
    return [[c['source_start_frame'] / fps, c['source_end_frame'] / fps] for c in ordered]


def _boundaries(keeps):
    return sorted(t for keep in keeps for t in keep)


def compare_cuts(planned, reference, words=(), window=2.0, fps=24):
    """How far the planned boundaries are from the editor's own, and whether a cut lands inside a word."""
    plan_points, reference_points = _boundaries(planned), _boundaries(reference)
    errors = []
    missed = []
    for point in reference_points:
        nearest = min(plan_points, key=lambda p: abs(p - point), default=None)
        if nearest is None or abs(nearest - point) > window:
            missed.append(round(point, 3))
        else:
            errors.append(abs(nearest - point) * 1000)
    extra = [round(p, 3) for p in plan_points if not any(abs(p - r) <= window for r in reference_points)]
    tolerance = 1 / fps / 2
    inside = [dict(time=round(p, 3), word=w['text']) for p in plan_points for w in words if w['start'] + tolerance < p < w['end'] - tolerance]
    errors.sort()
    return dict(reference_boundaries=len(reference_points), matched=len(errors), missed=missed, extra=extra,
                median_ms=round(statistics.median(errors), 1) if errors else None,
                p90_ms=round(errors[int(len(errors) * 0.9) - 1 if len(errors) >= 10 else -1], 1) if errors else None,
                within_one_frame=sum(e <= 1000 / fps for e in errors), cuts_inside_words=inside)


def main(argv):
    """python auto_edit.py plan recipe.json sources.json [answers.json] | catalog

    sources.json maps a file name or form question to {duration, fps, words}.
    """
    if len(argv) >= 2 and argv[1] == 'catalog':
        print(json.dumps(catalog(), ensure_ascii=False, indent=1))
        return 0
    if len(argv) < 4 or argv[1] != 'plan':
        print(main.__doc__, file=sys.stderr)
        return 2
    loaded = []
    for path in argv[2:5]:
        with open(path, encoding='utf-8') as handle:
            loaded.append(json.load(handle))
    print(json.dumps(plan_edit(*loaded), ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))

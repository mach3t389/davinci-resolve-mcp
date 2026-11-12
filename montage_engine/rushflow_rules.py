"""Rules written in Rushflow, read by the engine (doc 25, section 4).

Rushflow stores the directives in layers (organisation, video type, project, video) and resolves them: the result of its
`get_montage_directives` action (or the copy frozen in a montage job) holds `values`, `provenance` (the layer each value
comes from) and the free-written `instructions`. `load_rules` reads that JSON and hands `values` to the engine, which
until now used its own built-in values (series.py). `describe_rules` says, rule by rule, what the engine does with it:

  applied    the engine checks it or applies it on the copy it creates
  info       the engine measures it and mentions it, but never treats it as a defect to correct (Alexis, 12 Nov 2026)
  assistant  the engine cannot do it (the scripting API has no volume automation); the user's assistant follows it
  unread     known to Rushflow, not read by the engine yet
  unknown    not a rule the engine has ever heard of: reported, never dropped silently

Free-written instructions are never interpreted by the engine: they are listed for the assistant.
"""
import copy
import json

from .series import GENERAL

APPLIED = 'applied'
INFO = 'info'
ASSISTANT = 'assistant'
UNREAD = 'unread'
UNKNOWN = 'unknown'

# Rule -> (status, note). Read from the engine code (checks.py, passes.py, audio_rules.py, cameras.py, panels.py).
RULE_STATUS = {
    'sync.tolerance_frames': (APPLIED, 'image = son'),
    'cuts.silence_max_extend_frames': (APPLIED, 'coupes calees sur le silence'),
    'cuts.silence_margin_db': (APPLIED, 'coupes calees sur le silence'),
    'cuts.remnant_max_frames': (APPLIED, 'restes de quelques images'),
    'cuts.wave_suspect_margin_db': (APPLIED, 'coupes sur du son'),
    'cuts.wave_probe_frames': (APPLIED, 'coupes sur du son'),
    'cuts.keep_pause_seconds': (UNREAD, 'le moteur ne raccourcit plus un montage fini (12 nov.); a revoir pour un premier montage'),
    'cuts.audio_lead_frames': (APPLIED, 'coupe en J sous un plan d\'appui'),
    'cameras.min_shot_seconds': (APPLIED, 'plan visible minimum'),
    'cameras.main_share': (APPLIED, 'part de la camera principale'),
    'cameras.share_tolerance': (APPLIED, 'part de la camera principale'),
    'cameras.main_min_seconds': (APPLIED, 'alternance des cameras'),
    'cameras.secondary_seconds': (APPLIED, 'alternance des cameras'),
    'cameras.max_same_camera_seconds': (INFO, 'duree sur une camera : information, pas un defaut'),
    'cameras.switch_on_jump_cut': (UNREAD, 'pas encore lue par le moteur'),
    'overlays': (APPLIED, 'fondus des objets poses par-dessus'),
    'broll.small_gap_frames': (APPLIED, 'trous entre plans d\'appui'),
    'panels.chain_gap_frames': (APPLIED, 'enchainement des panneaux'),
    'panels.crossfade_frames': (APPLIED, 'enchainement des panneaux'),
    'pauses.audio_fade_frames': (UNREAD, 'le moteur utilise 3 images'),
    'tracks.pairs': (APPLIED, 'pistes de voix et de cameras'),
    'voice_isolation': (UNREAD, 'copiee depuis la timeline source'),
    'audio.target_lufs': (APPLIED, 'niveau de la voix'),
    'audio.music_under_voice_db': (ASSISTANT, 'impossible par l\'API : l\'assistant la suit'),
}


class RulesError(ValueError):
    """The JSON is not a set of resolved directives."""


def _leaves(node, prefix=''):
    for key, value in node.items():
        path = f'{prefix}.{key}' if prefix else key
        if isinstance(value, dict) and value and path != 'overlays':
            yield from _leaves(value, path)
        else:
            yield path, value


def status_of(path):
    """(status, note) of a rule path; overlay fades keyed by a name prefix (overlays.Visuels.fade_in_frames) count as `overlays`."""
    if path in RULE_STATUS:
        return RULE_STATUS[path]
    if path.startswith('overlays'):
        return RULE_STATUS['overlays']
    return UNKNOWN, 'regle inconnue du moteur'


def _fill(base, defaults, path=''):
    """Deep-fill `base` with the engine's defaults where it has no value; returns the paths filled."""
    filled = []
    for key, value in defaults.items():
        p = f'{path}.{key}' if path else key
        if key not in base:
            base[key] = copy.deepcopy(value)
            filled += [leaf for leaf, _ in _leaves({key: value}, path)] if isinstance(value, dict) else [p]
        elif isinstance(value, dict) and isinstance(base[key], dict):
            filled += _fill(base[key], value, p)
    return filled


def load_rules(source):
    """source: a path to a JSON file, a JSON string or a dict. Accepts the result of `get_montage_directives`
    ({values, provenance, instructions, ...}), the copy frozen in a montage job, or either wrapped in {"directives": ...}.
    -> dict(values, provenance, instructions, filled): `values` is ready for checks.run_all / passes.run_pass; `filled`
    lists the rules Rushflow did not state and for which the engine's own default was used."""
    data = source
    if isinstance(source, str):
        try:
            if source.lstrip().startswith('{'):
                data = json.loads(source)
            else:
                with open(source, encoding='utf8') as f:
                    data = json.load(f)
        except OSError as exc:
            raise RulesError(f'cannot read {source}: {exc}')
        except ValueError as exc:
            raise RulesError(f'not valid JSON: {exc}')
    if not isinstance(data, dict):
        raise RulesError('expected a JSON object')
    if isinstance(data.get('directives'), dict):
        data = data['directives']
    values = data.get('values')
    if not isinstance(values, dict) or not values:
        raise RulesError('no "values": give the result of get_montage_directives or a montage job\'s directives')
    values = copy.deepcopy(values)
    provenance = dict(data.get('provenance') or {})
    texts = data.get('instructions') or []
    if isinstance(texts, str):
        texts = [{'level': '', 'text': texts}]
    instructions = [dict(level=t.get('level', ''), text=t.get('text', '')) for t in texts
                    if isinstance(t, dict) and str(t.get('text', '')).strip()]
    filled = _fill(values, GENERAL)
    return dict(values=values, provenance=provenance, instructions=instructions, filled=filled)


def describe_rules(values, provenance=None):
    """One row per rule that has a value: {rule, value, level, status, note}. A rule left empty (None) is "no rule" and
    is not listed. Sorted so the rules the engine does NOT apply come last."""
    provenance = provenance or {}
    rows = []
    for path, value in _leaves(values):
        if value is None:
            continue
        status, note = status_of(path)
        rows.append(dict(rule=path, value=value, level=provenance.get(path, ''), status=status, note=note))
    order = {APPLIED: 0, INFO: 1, ASSISTANT: 2, UNREAD: 3, UNKNOWN: 4}
    return sorted(rows, key=lambda r: (order[r['status']], r['rule']))


def summary(rows):
    counts = {}
    for r in rows:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    return counts

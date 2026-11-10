"""Layered montage directives with provenance, and track roles.

Levels (each overrides only what it states; Rushflow stores the same structure, see doc 25):
  general (organisation) -> video type -> series/project -> video.
`resolve_directives` returns the merged values and, for every leaf, the level it came from.
"""
import copy
import re

LEVELS = ('general', 'type', 'series', 'video')

GENERAL = {
    'sync': {'tolerance_frames': 1},
    'cuts': {'silence_max_extend_frames': 8, 'silence_margin_db': 8.0, 'remnant_max_frames': 3,
             'wave_suspect_margin_db': 6.0, 'wave_probe_frames': 2},
    'cameras': {'switch_on_jump_cut': True, 'min_shot_seconds': None, 'main_share': None, 'share_tolerance': 0.08,
                'max_same_camera_seconds': None},
    'overlays': {'default': {'fade_in_frames': 12, 'fade_out_frames': 18, 'pad_frames': 12}},
    'broll': {'small_gap_frames': 12},          # a gap this small between two b-roll clips is a mistake
    'pauses': {'audio_fade_frames': 3},
    'tracks': {'pairs': [[1, 1], [2, 2]]},     # (audio track, camera video track) heard/seen together
}

# Examples of the other levels (used as defaults when no profile file is given).
VIDEO_TYPES = {
    'interview': {'cameras': {'min_shot_seconds': 2.5}},
}
SERIES = {
    'masterclass': {
        'cameras': {'main_share': 0.75, 'max_same_camera_seconds': 10.0, 'main_min_seconds': 6,
                    'secondary_seconds': [3, 10]},
        'panels': {'chain_gap_frames': 24, 'crossfade_frames': 12},
        'overlays': {'default': {'fade_in_frames': 12, 'fade_out_frames': 18, 'pad_frames': 12},
                     'Visuels': {'fade_in_frames': 32, 'fade_out_frames': 36, 'pad_frames': 24}},
        'voice_isolation': 70,
    },
}


def _merge(base, over, path, prov, level):
    for k, v in over.items():
        p = path + (k,)
        if isinstance(v, dict):
            if not isinstance(base.get(k), dict):
                base[k] = {}
            _merge(base[k], v, p, prov, level)
        else:
            base[k] = copy.deepcopy(v)
            prov['.'.join(p)] = level


def resolve_directives(general=None, type_=None, series=None, video=None):
    """-> (values, provenance). Provenance maps 'cameras.main_share' -> 'series' etc."""
    values, prov = {}, {}
    for level, layer in zip(LEVELS, (GENERAL if general is None else general, type_, series, video)):
        if layer:
            _merge(values, layer, (), prov, level)
    return values, prov


def explain(values, prov, prefix=''):
    """Human readable list (for a directives page): 'cameras.main_share = 0.75  (series)'."""
    rows = []
    for k, v in sorted(prov.items()):
        if k.startswith(prefix):
            node = values
            for part in k.split('.'):
                node = node[part]
            rows.append(f'{k} = {node}  ({v})')
    return rows


# ---- track roles -------------------------------------------------------------------------------------------------
ROLE_PATTERNS = [
    ('camera_secondary', r'cam[ée]ra\s*2|slider|secondaire'),
    ('camera_main', r'cam[ée]ra\s*1|principale|^cam'),
    ('broll', r"plans?\s*d.appui|stock|b-?roll"),
    ('graphic', r'motion|panneau|visuel|video\s*\d|titre|anim'),
]


def role_of(track_name, kind='video'):
    """Role of a track from its NAME (never from its number). Unknown video tracks above the cameras are graphics."""
    n = (track_name or '').strip().lower()
    if kind == 'audio':
        return 'voice' if (not n or re.search(r'voix|voice|audio\s*[12]\b', n)) else 'audio'
    for role, pat in ROLE_PATTERNS:
        if re.search(pat, n):
            return role
    return 'graphic'


def roles_of(snapshot):
    """{('video', 2): 'camera_main', ...} for a Snapshot; reports every track so a missing role is visible."""
    return {(kind, i): role_of(name, kind) for (kind, i), name in sorted(snapshot.track_names.items())}


def missing_roles(snapshot, needed=('camera_main', 'camera_secondary')):
    present = set(roles_of(snapshot).values())
    return [r for r in needed if r not in present]


def overlay_params(values, clip_name):
    """Fade parameters for a graphic clip: a name-prefix entry ('Visuels') wins over 'default'."""
    table = values.get('overlays', {})
    default = table.get('default', {})
    for key, params in table.items():
        if key != 'default' and clip_name.startswith(key):
            return {**default, **params}
    return dict(default)

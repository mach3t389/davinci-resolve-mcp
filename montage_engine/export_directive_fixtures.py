"""Records results of the Python directives engine so the TypeScript port in Rushflow
(`shared/montage/directives.ts`) can replay them and require identical output.

Usage:  python -m montage_engine.export_directive_fixtures <output.json>
Standard library only.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from montage_engine.series import (GENERAL, SERIES, VIDEO_TYPES, explain, overlay_params,  # noqa: E402
                                   resolve_directives)

CASES = {
    'general only': dict(),
    'interview type': dict(type_=VIDEO_TYPES['interview']),
    'masterclass series': dict(series=SERIES['masterclass']),
    'interview + masterclass': dict(type_=VIDEO_TYPES['interview'], series=SERIES['masterclass']),
    'video override': dict(type_=VIDEO_TYPES['interview'], series=SERIES['masterclass'],
                           video={'cameras': {'main_share': 0.6}, 'overlays': {'Titre': {'fade_in_frames': 6}}}),
    'custom general': dict(general={'sync': {'tolerance_frames': 2}, 'cameras': {'main_share': None}}),
}
KEYS = {'type_': 'type'}


def main(out):
    cases = []
    for name, kw in CASES.items():
        values, prov = resolve_directives(**kw)
        layers = {KEYS.get(k, k): v for k, v in kw.items()}
        cases.append(dict(name=name, layers=layers, values=values, provenance=prov, explain=explain(values, prov),
                          overlay_params={n: overlay_params(values, n) for n in ('Visuels03_v001.mov', 'Lili01_v002.mov', 'Titre9.mov')}))
    Path(out).write_text(json.dumps(dict(general=GENERAL, cases=cases), ensure_ascii=False, indent=1), encoding='utf8')
    print(len(cases), 'cases ->', out)


if __name__ == '__main__':
    main(sys.argv[1])

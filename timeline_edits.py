"""Frame-exact OTIO planning. All intervals use an exclusive end frame."""
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import opentimelineio as otio


def frame(value, rate):
    value = float(value.rescaled_to(rate).value)
    rounded = round(value)
    if not math.isfinite(value) or abs(value - rounded) > 1e-5:
        raise ValueError('Subframe positions are unsupported; use a frame-aligned timeline.')
    return rounded


def timestamp_frames(value, rate):
    """Elapsed seconds or M:SS / H:MM:SS, rounded half-up to a video frame."""
    try:
        parts = str(value).split(':')
        if not 1 <= len(parts) <= 3:
            raise ValueError('Use seconds, M:SS or H:MM:SS, not a source timecode.')
        numbers = [Decimal(p) for p in parts]
        if any(not n.is_finite() or n < 0 for n in numbers):
            raise ValueError('Timestamp must be finite and nonnegative.')
        if any(n != int(n) for n in numbers[:-1]) or any(n >= 60 for n in numbers[1:]):
            raise ValueError('Invalid timestamp component.')
        seconds = sum(n * (60 ** i) for i, n in enumerate(reversed(numbers)))
        return int((seconds * Decimal(str(rate))).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise ValueError('Invalid elapsed timestamp.') from exc


def normalize_ranges(ranges, duration):
    if not ranges or len(ranges) > 1000:
        raise ValueError('Provide 1 to 1000 exact ranges.')
    result = []
    for r in ranges:
        a, b = r['start_frame'], r['end_frame']
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= duration:
            raise ValueError('Ranges must be integer frames: 0 <= start < end <= duration.')
        result.append({'start_frame': a, 'end_frame': b, 'label': str(r.get('label', ''))[:200]})
    result.sort(key=lambda r: r['start_frame'])
    if any(a['end_frame'] > b['start_frame'] for a, b in zip(result, result[1:])):
        raise ValueError('Overlapping ranges are ambiguous; merge them explicitly.')
    return result


def validate_timeline(timeline, rate):
    if timeline.tracks.source_range is not None or timeline.tracks.effects:
        raise ValueError('Trimmed/effected timeline stacks are unsupported.')
    for track in timeline.tracks:
        if not isinstance(track, otio.schema.Track) or track.kind not in ('Video', 'Audio'):
            raise ValueError('Only flat video/audio tracks are supported.')
        if track.source_range is not None or track.effects:
            raise ValueError('Trimmed/effected tracks are unsupported.')
        for item in track:
            if not isinstance(item, (otio.schema.Clip, otio.schema.Gap)):
                raise ValueError('Transitions and nested/multicam compositions are unsupported.')
            frame(item.duration(), rate)
            if isinstance(item, otio.schema.Clip):
                if not isinstance(item.media_reference, otio.schema.ExternalReference):
                    raise ValueError('Only external file media references are supported.')
                for effect in item.effects:
                    if isinstance(effect, otio.schema.TimeEffect):
                        raise ValueError('Speed changes and freeze frames are unsupported.')
                    data = effect.metadata.get('Resolve_OTIO', {})
                    params = data.get('Parameters', [])
                    changed = any(p.get('Key Frames') or
                                  p.get('Parameter Value') != p.get('Default Parameter Value')
                                  for p in params if p.get('Parameter ID') != 'eq band index')
                    if data.get('Enabled', True) and changed:
                        raise ValueError('Configured clip effects require manual review; use a raw timeline.')


def transform(timeline, rate, ranges, mode):
    if mode not in ('split_all', 'keep_with_gaps'):
        raise ValueError('Mode must be split_all or keep_with_gaps.')
    validate_timeline(timeline, rate)
    total = frame(timeline.duration(), rate)
    ranges = normalize_ranges(ranges, total)
    boundaries = sorted({r[k] for r in ranges for k in ('start_frame', 'end_frame')})
    output = timeline.deepcopy()
    link_ids = {}
    next_link = 1
    pieces = []
    for ti, (original, target) in enumerate(zip(timeline.tracks, output.tracks)):
        target.clear()
        cursor = 0
        for item in original:
            length = frame(item.duration(), rate)
            cuts = [cursor] + [b for b in boundaries if cursor < b < cursor + length] + [cursor + length]
            for a, b in zip(cuts, cuts[1:]):
                selected = any(r['start_frame'] <= a and b <= r['end_frame'] for r in ranges)
                if isinstance(item, otio.schema.Gap) or (mode == 'keep_with_gaps' and not selected):
                    target.append(otio.schema.Gap(duration=otio.opentime.RationalTime(b - a, rate)))
                    continue
                piece = item.deepcopy()
                src = item.trimmed_range().start_time
                piece.source_range = otio.opentime.TimeRange(
                    src + otio.opentime.RationalTime(a - cursor, rate).rescaled_to(src.rate),
                    otio.opentime.RationalTime(b - a, rate).rescaled_to(src.rate))
                # Clip markers are in source coordinates. Trim them to each new piece.
                markers = list(piece.markers)
                piece.markers.clear()
                for marker in markers:
                    intersection = marker.marked_range.intersection(piece.source_range)
                    if intersection is not None and intersection.duration.value > 0:
                        new_marker = marker.deepcopy()
                        new_marker.marked_range = intersection
                        piece.markers.append(new_marker)
                metadata = piece.metadata.get('Resolve_OTIO', {})
                group = metadata.get('Link Group ID')
                if group is not None:
                    key = (group, a, b)
                    if key not in link_ids:
                        link_ids[key] = next_link
                        next_link += 1
                    metadata['Link Group ID'] = link_ids[key]
                target.append(piece)
                pieces.append({'track': ti, 'kind': original.kind, 'start_frame': a,
                               'end_frame': b, 'name': item.name, 'selected': selected})
            cursor += length
    return output, pieces, ranges


def structure(timeline, rate):
    """Only compare timing, media, tracks and link partitions, not importer metadata."""
    result = []
    for track in timeline.tracks:
        cursor = 0
        clips = []
        for item in track:
            length = frame(item.duration(), rate)
            if isinstance(item, otio.schema.Clip):
                clips.append([cursor, cursor + length, item.media_reference.target_url,
                              round(float(item.trimmed_range().start_time.rescaled_to(rate).value), 5)])
            cursor += length
        result.append({'kind': track.kind, 'clips': clips})
    return result


def link_partitions(timeline):
    groups = {}
    for ti, track in enumerate(timeline.tracks):
        ci = 0
        for item in track:
            if isinstance(item, otio.schema.Clip):
                group = item.metadata.get('Resolve_OTIO', {}).get('Link Group ID')
                if group is not None:
                    groups.setdefault(group, []).append((ti, ci))
                ci += 1
    return sorted(sorted(g) for g in groups.values() if len(g) > 1)

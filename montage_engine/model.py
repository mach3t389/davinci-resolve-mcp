"""Plain snapshot of a timeline. All frames are relative to the timeline start (not the 01:00:00:00 offset)."""
from dataclasses import dataclass, field

FPS = 24


@dataclass
class Clip:
    kind: str                 # 'video' | 'audio'
    track: int                # 1-based track index
    track_name: str
    start: int                # timeline frame (inclusive)
    end: int                  # timeline frame (exclusive)
    file: str = ''            # media file path ('' for generators / compound clips without media)
    name: str = ''
    src_start: int = 0        # first source frame used (in the media's own frame rate)
    enabled: bool = True
    color: str = ''
    media_fps: float = FPS
    has_media: bool = True

    @property
    def length(self):
        return self.end - self.start

    def src_at(self, f):
        """Source frame shown/heard at timeline frame f (assumes 100 % speed)."""
        return self.src_start + (f - self.start)

    def continues(self, other):
        """True when `other` directly continues this clip from the same take (no jump in the source)."""
        return (self.file == other.file and self.end == other.start
                and other.src_start == self.src_start + self.length)


@dataclass
class Snapshot:
    name: str = ''
    fps: int = FPS
    start_tc_frames: int = 0
    out_frame: int = 0                     # end of the In-Out range ("Out" marker) or the timeline end
    clips: list = field(default_factory=list)
    markers: dict = field(default_factory=dict)      # frame -> {name, color, note, duration}
    track_names: dict = field(default_factory=dict)  # (kind, index) -> name

    def track(self, kind, index):
        return sorted((c for c in self.clips if c.kind == kind and c.track == index), key=lambda c: c.start)

    def tracks(self, kind):
        return sorted({c.track for c in self.clips if c.kind == kind} |
                      {i for (k, i) in self.track_names if k == kind})

    def at(self, kind, index, f):
        for c in self.track(kind, index):
            if c.start <= f < c.end:
                return c
        return None


def tc(f, fps=FPS):
    """mm:ss.ss label of a frame count, used in findings."""
    f = int(f)
    per_min = fps * 60
    return f'{f // per_min:02d}:{(f % per_min) / fps:05.2f}'


def snapshot_from_resolve(t, out_marker_names=('Out', 'Out montage')):
    """Build a Snapshot from a Resolve timeline object. Read only."""
    st = t.GetStartFrame()
    snap = Snapshot(name=t.GetName(), start_tc_frames=st)
    try:
        snap.fps = int(round(float(t.GetSetting('timelineFrameRate'))))
    except (TypeError, ValueError):
        snap.fps = FPS
    markers = {}
    for k, v in (t.GetMarkers() or {}).items():
        markers[int(float(k))] = dict(name=v.get('name', ''), color=v.get('color', ''), note=v.get('note', ''),
                                      duration=v.get('duration', 1))
    snap.markers = markers
    outs = [f for f, m in markers.items() if m['name'] in out_marker_names]
    snap.out_frame = outs[0] if outs else int(t.GetEndFrame() - st)
    for kind in ('video', 'audio'):
        for i in range(1, t.GetTrackCount(kind) + 1):
            name = t.GetTrackName(kind, i)
            snap.track_names[(kind, i)] = name
            for x in t.GetItemListInTrack(kind, i) or []:
                mp = x.GetMediaPoolItem()
                try:
                    fps = float(mp.GetClipProperty('FPS')) if mp else snap.fps
                except (TypeError, ValueError):
                    fps = snap.fps
                snap.clips.append(Clip(
                    kind=kind, track=i, track_name=name, start=int(x.GetStart() - st), end=int(x.GetEnd() - st),
                    file=mp.GetClipProperty('File Path') if mp else '', name=x.GetName(),
                    src_start=int(x.GetLeftOffset()), enabled=bool(x.GetClipEnabled()),
                    color=x.GetClipColor() or '', media_fps=fps, has_media=mp is not None))
    return snap

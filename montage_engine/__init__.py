"""Montage engine: reusable rules, checks and edit passes extracted from the Masterclass "Lili" trial (Nov 2026).

Pure logic (no Resolve needed) is separated from the thin Resolve adapters so it can be unit tested:
  model    - a plain snapshot of a timeline (clips per track, frames relative to the timeline start)
  series   - layered directives (general -> video type -> series/project -> video) with provenance, track roles
  anchors  - transcription anchored on SOURCE files, words and markers re-placed on any timeline
  checks   - the read-only checks run after every pass
  passes   - generic OTIO edit pass (remove ranges, insert pauses) and silence snapping
"""

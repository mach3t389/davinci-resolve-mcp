"""Alpha profile of every panel/visual movie used on the graphic tracks of a timeline (all video tracks except the
cameras and "Plans d'appui"): first fully visible frame, frame where its fade-out starts, last visible frame.
Used to chain two panels: the second must be fully visible when the first starts to fade. Read-only.
Usage: alpha_profile.py <timeline>  ->  work/alpha_profiles.json"""
import json, os, re, subprocess, sys
sys.path.insert(0, r'D:\Vibe coding\davinci-resolve-mcp')
import resolve_safe_mcp as r
W = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'work'); os.makedirs(W, exist_ok=True)
_, p = r.project(); t = {x.GetName(): x for x in r.timelines(p)}[sys.argv[1]]
files = {}
for i in range(1, t.GetTrackCount('video') + 1):
    name = t.GetTrackName('video', i)
    if name.startswith('Camera') or name == "Plans d'appui": continue
    for x in t.GetItemListInTrack('video', i):
        m = x.GetMediaPoolItem()
        if m: files.setdefault(m.GetName(), m.GetClipProperty('File Path'))
out = {}
for name, path in sorted(files.items()):
    res = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-vf', 'alphaextract,scale=160:90,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-',
                          '-f', 'null', '-'], capture_output=True, text=True)
    vals = [float(v) for v in re.findall(r'YAVG=([\d.]+)', res.stdout)]
    if not vals: out[name] = None; print(name, 'pas de canal alpha'); continue
    mx = max(vals); full = [k for k, v in enumerate(vals) if v >= 0.98 * mx]; vis = [k for k, v in enumerate(vals) if v > 0.02 * mx]
    out[name] = dict(n=len(vals), in_full=full[0], out_start=full[-1] + 1, first_visible=vis[0], last_visible=vis[-1])
    print(name, out[name])
json.dump(out, open(os.path.join(W, 'alpha_profiles.json'), 'w'), indent=1)

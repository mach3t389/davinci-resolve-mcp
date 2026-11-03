"""Deterministic, frame-based long-shot camera schedule."""
import math

def runs_from_clips(clips, start, end):
    runs=[]
    previous=None
    for c in sorted(clips,key=lambda x:x['start']):
        a,z=max(start,int(c['start'])),min(end,int(c['end']))
        if a>=z: continue
        contiguous=(previous and previous['end']==c['start'] and
                    previous['name']==c['name'] and
                    abs(previous['source_end_frame']-c['source_start_frame'])<=1)
        if runs and contiguous: runs[-1]['end']=z
        else: runs.append(dict(start=a,end=z))
        previous=c
    return runs

def schedule(runs,fps,fraction,rhythm='long'):
    shots=[]; totals={1:0,2:0,3:0}; previous=None; previous_end=None
    def alternate():
        return 3 if totals[3]<=2*totals[2] else 2
    def add(a,z,cam):
        if a>=z:return
        shots.append(dict(start=a,end=z,camera=cam))
        totals[cam]+=z-a
    for run in runs:
        a,z=run['start'],run['end']; length=z-a
        if previous_end!=a:previous=None
        first=alternate() if previous==1 else 1
        single = length<20*fps if rhythm=='10-15' else (length<36*fps or (length<54*fps and first==1))
        if single:
            add(a,z,first)
        else:
            if rhythm=='10-15':
                pairs=max(1,round(length/(36*fps)))
                alt=min(round(length*fraction),15*fps*pairs)
                alt=max(alt,10*fps*pairs)
                alt=min(alt,length-10*fps*pairs)
            else:
                pairs=max(1,math.ceil(length/(96*fps)))
                alt=min(round(length*fraction),32*fps*pairs)
                alt=max(alt,18*fps*pairs)
                alt=min(alt,length-18*fps*pairs)
            wide=length-alt
            widths=[wide//pairs+(i<wide%pairs) for i in range(pairs)]
            alts=[alt//pairs+(i<alt%pairs) for i in range(pairs)]
            cursor=a
            for i in range(pairs):
                camera=alternate()
                sequence=[(1,widths[i]),(camera,alts[i])] if first==1 else [(camera,alts[i]),(1,widths[i])]
                for cam,duration in sequence:
                    add(cursor,cursor+duration,cam);cursor+=duration
        previous=shots[-1]['camera'];previous_end=z
    return shots,totals

def make_plan(clips,start,end,fps=24,rhythm='long'):
    if rhythm not in ('long','10-15'):raise ValueError('Unknown camera rhythm.')
    runs=runs_from_clips(clips,start,end)
    candidates=[schedule(runs,fps,n/1000,rhythm) for n in range(200,501)]
    shots,totals=min(candidates,key=lambda x:abs(x[1][1]/sum(x[1].values())-2/3))
    return dict(start=start,end=end,fps=fps,rhythm=rhythm,runs=runs,shots=shots,
                seconds={str(k):v/fps for k,v in totals.items()},
                percentages={str(k):100*v/sum(totals.values()) for k,v in totals.items()},
                short_shots=[s for s in shots if s['end']-s['start']<(10 if rhythm=='10-15' else 18)*fps])

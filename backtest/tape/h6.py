import os as _os
def _chdir_cache():
    d=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"..","cache","tape"); _os.makedirs(d,exist_ok=True); _os.chdir(d)
# H6 (pre-registered Session 36 after H5 failed): H5 + require (ask - bid) <= 0.03 at entry.
import h5
LO,HI,MAXSPREAD=0.70,0.85,0.03
def entries(d, first_only=True):
    m=d["m"]; yes=m["result"]=="yes"; out=[]
    for ts,yb,ya in sorted(d["candles"]):
        if ts>d["cutoff"] or yb is None or ya is None: continue
        if not (0<yb<ya<1) or ya-yb>MAXSPREAD+1e-9: continue
        noa=1-yb; qy=LO<=ya<HI; qn=LO<=noa<HI
        if qy==qn: continue
        p,win=(ya,yes) if qy else (noa,not yes)
        fee=0.07*p*(1-p); out.append((((1 if win else 0)-p-fee)/(p+fee),ts,p))
        if first_only: break
    return out
h5.entries=entries
if __name__=="__main__":
    _chdir_cache()
    h5.run(False, True, "H6 PRIMARY in-sample (Jul30-Sep27), first qualifying hour, spread<=3c")
    h5.run(True, True, "H6 secondary OOS (May-Jul, contaminated by H5 inspection)")

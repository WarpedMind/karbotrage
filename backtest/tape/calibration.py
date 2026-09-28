import os as _os; _D=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"..","cache","tape"); _os.makedirs(_D,exist_ok=True); _os.chdir(_D)
# Sanity check used to rule out a data bug after H5 failed: calibration of the candle MID vs outcome.
import json,glob,collections,sys
hist = (sys.argv[1] if len(sys.argv)>1 else "hist")=="hist"
for label,pick in [("last candle >=24h pre-cutoff", lambda cs,cut: [c for c in cs if c[0]<=cut-86400][-1:]),
                   ("last candle pre-cutoff", lambda cs,cut: [c for c in cs if c[0]<=cut][-1:]),
                   ("first two-sided candle", lambda cs,cut: cs[:1])]:
    b=collections.defaultdict(lambda:[0,0,0.0])
    for f in glob.glob("candles/*.json"):
        d=json.load(open(f))
        if d["hist"]!=hist: continue
        cs=[c for c in sorted(d["candles"]) if c[1] is not None and c[2] is not None and 0<c[1]<c[2]<1]
        sel=pick(cs,d["cutoff"])
        if not sel: continue
        ts,yb,ya=sel[0]; mid=(yb+ya)/2; k=min(int(mid*10),9)
        x=b[k]; x[0]+=1; x[1]+= d["m"]["result"]=="yes"; x[2]+=mid
    print("==",label)
    for k in sorted(b): n,w,s=b[k]; print(f"  mid {k/10:.1f}-{(k+1)/10:.1f}: n={n:5d} avg mid={s/n:.3f} yes-rate={w/n:.3f}")

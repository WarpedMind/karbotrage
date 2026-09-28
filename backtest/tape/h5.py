import os as _os
def _chdir_cache():
    d=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"..","cache","tape"); _os.makedirs(d,exist_ok=True); _os.chdir(d)
import json, glob, collections, random, datetime as dt, statistics, sys
iso=lambda t: dt.datetime.fromisoformat(t.replace("Z","+00:00")).timestamp()
LO,HI=0.70,0.85
def entries(d, first_only=True):
    m=d["m"]; yes=m["result"]=="yes"; out=[]
    for ts,yb,ya in sorted(d["candles"]):
        if ts>d["cutoff"] or yb is None or ya is None: continue
        if not (0<yb<ya<1): continue
        noa=1-yb
        qy=LO<=ya<HI; qn=LO<=noa<HI
        if qy==qn: continue
        p, win = (ya, yes) if qy else (noa, not yes)
        fee=0.07*p*(1-p)
        out.append(((1 if win else 0)-p-fee)/(p+fee))
        out[-1]=(out[-1], ts, p)
        if first_only: break
    return out
def run(src_hist, first_only=True, label=""):
    bydate=collections.defaultdict(list); hold=[]; perday=collections.Counter(); prices=[]
    for f in glob.glob("candles/*.json"):
        d=json.load(open(f))
        if d["hist"]!=src_hist: continue
        e=entries(d, first_only)
        if not e: continue
        r=sum(x[0] for x in e)/len(e)
        day=d["m"]["settlement_ts"][:10]; bydate[day].append(r); perday[day]+=1
        hold.append((iso(d["m"]["settlement_ts"])-e[0][1])/3600); prices.append(e[0][2])
    ks=sorted(bydate); allv=[v for k in ks for v in bydate[k]]
    rnd=random.Random(55); bs=[]
    for _ in range(4000):
        vs=[]
        for _ in ks: vs+=bydate[ks[rnd.randrange(len(ks))]]
        bs.append(sum(vs)/len(vs))
    bs.sort(); P=sum(1 for x in bs if x<=0)/len(bs)
    print(f"{label}: eq-wt net ret {100*sum(allv)/len(allv):+.2f}%  95%CI[{100*bs[100]:+.2f},{100*bs[3900]:+.2f}]  one-sided P(<=0)={P:.4f}  markets={len(allv)} dates={len(ks)}")
    print(f"   win-rate {sum(1 for v in allv if v>0)/len(allv):.3f}  mean entry price {statistics.mean(prices):.3f}  median hold {statistics.median(hold):.1f}h  p90 hold {sorted(hold)[int(.9*len(hold))]:.0f}h  qualifying markets/day (sample) median {statistics.median(perday.values())}")
    t=len(ks)//3
    for i,s in enumerate([ks[:t],ks[t:2*t],ks[2*t:]]):
        vs=[v for k in s for v in bydate[k]]
        rnd=random.Random(i); b2=[]
        for _ in range(1000):
            w=[]
            for _ in s: w+=bydate[s[rnd.randrange(len(s))]]
            b2.append(sum(w)/len(w))
        b2.sort(); print(f"   period{i+1} {s[0]}..{s[-1]} {100*sum(vs)/len(vs):+.2f}% CI[{100*b2[25]:+.2f},{100*b2[975]:+.2f}] P={sum(1 for x in b2 if x<=0)/1000:.3f} n={len(vs)}")
if __name__=="__main__":
    _chdir_cache()
    run(True, True, "H5 PRIMARY  OOS (May-Jul), first qualifying hour")
    run(False, True, "H5 replication in-sample (Jul30-Sep27), first qualifying hour")
    run(True, False, "secondary OOS, all qualifying hours averaged per market")

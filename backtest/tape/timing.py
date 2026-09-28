import os as _os; _D=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"..","cache","tape"); _os.makedirs(_D,exist_ok=True); _os.chdir(_D)
import json,glob,collections,datetime as dt,random,sys
U=json.load(open("universe.json")); SER=U["series"]
d_=sys.argv[1]; cats=set(sys.argv[2].split("|"))
def iso(t): return dt.datetime.fromisoformat(t.replace("Z","+00:00")).timestamp()
# per market eq-wt return for taker buys at [0.70,0.85), split by hours-before-cutoff
buckets={"<6h":(0,6),"6-12h":(6,12),"12-24h":(12,24),">24h":(24,1e9)}
per={b:collections.defaultdict(list) for b in buckets}
for f in glob.glob(d_+"/*.json"):
    d=json.load(open(f)); m=d["m"]; ser=m["event_ticker"].split("-")[0]
    if (SER.get(ser) or ["?"])[0] not in cats: continue
    yes=m["result"]=="yes"; day=m["settlement_ts"][:10]; cut=d["cutoff"]
    agg={b:[0.0,0.0] for b in buckets}
    for ts,c,yp,side in d["trades"]:
        p=yp if side=="yes" else 1-yp
        if not (0.70<=p<0.85): continue
        win= yes if side=="yes" else not yes
        ft=0.07*p*(1-p); h=(cut-iso(ts))/3600
        for b,(a,z) in buckets.items():
            if a<=h<z: agg[b][0]+=c*((1 if win else 0)-p-ft); agg[b][1]+=c*(p+ft)
    for b in buckets:
        if agg[b][1]>0: per[b][day].append(agg[b][0]/agg[b][1])
for b in buckets:
    ks=list(per[b]); allv=[v for k in ks for v in per[b][k]]
    if not allv: print(b,"none"); continue
    rnd=random.Random(1); bs=[]
    for _ in range(1000):
        vs=[]
        for _ in ks: vs+=per[b][ks[rnd.randrange(len(ks))]]
        bs.append(sum(vs)/len(vs))
    bs.sort(); print(f"{b:7s} eq-wt {100*sum(allv)/len(allv):+.2f}% CI[{100*bs[25]:+.2f},{100*bs[975]:+.2f}] n={len(allv)}")

import os as _os; _D=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"..","cache","tape"); _os.makedirs(_D,exist_ok=True); _os.chdir(_D)
import requests, json, time, datetime as dt, os, random, glob, collections, threading
from concurrent.futures import ThreadPoolExecutor
API="https://api.elections.kalshi.com/trade-api/v2"
U=json.load(open("universe.json")); SER=U["series"]
def iso(t): return dt.datetime.fromisoformat(t.replace("Z","+00:00")).timestamp() if t else None
M=[]
for f in sorted(glob.glob("settled_days/*.json")): M+=json.load(open(f))
M=[m for m in M if m["result"] in ("yes","no")]
ev=collections.defaultdict(list)
for m in M: ev[m["event_ticker"]].append(m)
def cat(e): return (SER.get(e.split("-")[0]) or ["?"])[0]
bycat=collections.defaultdict(list)
for e in ev: bycat[cat(e)].append(e)
rnd=random.Random(36)
chosen=[]
for c,es in bycat.items():
    rnd.shuffle(es); perser=collections.Counter(); k=0
    for e in es:
        s=e.split("-")[0]
        if perser[s]>=12: continue
        perser[s]+=1; chosen.append(e); k+=1
        if k>=300: break
tasks=[]
for e in chosen:
    ms=ev[e]; rnd.shuffle(ms)
    for m in ms[:6]:
        cutoff=min(x for x in [iso(m["settlement_ts"]),iso(m["expected_expiration_time"]),iso(m["close_time"])] if x)-7200
        op=iso(m["open_time"])
        if cutoff>op: tasks.append((m,int(op),int(cutoff)))
print("events",len(chosen),"markets",len(tasks),flush=True)
os.makedirs("trades",exist_ok=True)
local=threading.local()
def sess():
    if not hasattr(local,"s"):
        local.s=requests.Session(); local.s.headers["User-Agent"]="karbotrage-research/0.1"
    return local.s
def get(p,params):
    for i in range(10):
        try:
            r=sess().get(API+p,params=params,timeout=60)
            if r.status_code==200: return r.json()
        except requests.RequestException: pass
        time.sleep(1.5*(i+1))
    raise RuntimeError("fail "+str(params))
def work(t):
    m,op,cut=t; fn=f"trades/{m['ticker']}.json"
    if os.path.exists(fn): return
    tr=[];cur=None
    for _ in range(5):
        p={"ticker":m["ticker"],"min_ts":op,"max_ts":cut,"limit":1000}
        if cur:p["cursor"]=cur
        d=get("/markets/trades",p)
        for x in d.get("trades",[]):
            tr.append([x["created_time"],float(x["count_fp"]),float(x["yes_price_dollars"]),x["taker_side"]])
        cur=d.get("cursor")
        if not cur or not d.get("trades"): break
    json.dump({"m":m,"cutoff":cut,"trunc":bool(cur),"trades":tr},open(fn,"w"))
done=0
with ThreadPoolExecutor(6) as ex:
    for _ in ex.map(work,tasks):
        done+=1
        if done%500==0: print("done",done,flush=True)
print("finished",done)

import os as _os; _D=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"..","cache","tape"); _os.makedirs(_D,exist_ok=True); _os.chdir(_D)
import requests, json, time, datetime as dt, os, random, glob, collections, threading
from concurrent.futures import ThreadPoolExecutor
API="https://api.elections.kalshi.com/trade-api/v2"
CATS={"Climate and Weather","Commodities","Economics","Elections","Entertainment","Financials","Politics","Science and Technology"}
U=json.load(open("universe.json")); SER=U["series"]
def iso(t): return dt.datetime.fromisoformat(t.replace("Z","+00:00")).timestamp() if t else None
LO=iso("2026-05-01T00:00:00Z"); HI=iso("2026-07-30T00:00:00Z")
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
    raise RuntimeError("fail "+p+str(params))
keep=["ticker","event_ticker","open_time","close_time","occurrence_datetime","expected_expiration_time","settlement_ts","result","volume_fp","market_type","strike_type","title"]
pop=set()
for f in glob.glob("settled_days/*.json"):
    for m in json.load(open(f)): pop.add(m["event_ticker"].split("-")[0])
bycat=collections.defaultdict(list)
for s in sorted(pop):
    c=(SER.get(s) or ["?"])[0]
    if c in CATS: bycat[c].append(s)
print({c:len(v) for c,v in bycat.items()},flush=True)
rnd=random.Random(3636)
def series_events(s):
    evs=collections.defaultdict(list); cur=None
    for _ in range(30):
        p={"limit":1000,"series_ticker":s}
        if cur: p["cursor"]=cur
        d=get("/historical/markets",p); ms=d.get("markets",[])
        for m in ms:
            ct=iso(m["close_time"])
            if m.get("custom_strike") or m.get("result") not in ("yes","no"): continue
            try: v=float(m.get("volume_fp") or 0)
            except: v=0
            if LO<=ct<HI and v>=1000: evs[m["event_ticker"]].append({k:m.get(k) for k in keep})
        cur=d.get("cursor")
        if not cur or not ms or iso(ms[-1]["close_time"])<LO: break
    return evs
chosen={}
for c,sers in bycat.items():
    rnd.shuffle(sers); n=0
    with ThreadPoolExecutor(6) as ex:
        results=list(ex.map(series_events,sers[:120]))
    for s,evs in zip(sers[:120],results):
        es=list(evs); rnd.shuffle(es)
        for e in es[:12]:
            chosen[e]=evs[e]; n+=1
            if n>=300: break
        if n>=300: break
    print(c,"events",n,flush=True)
tasks=[]
for e,ms in chosen.items():
    rnd.shuffle(ms)
    for m in ms[:6]:
        cut=min(x for x in [iso(m["settlement_ts"]),iso(m["expected_expiration_time"]),iso(m["close_time"])] if x)-7200
        op=iso(m["open_time"])
        if cut>op: tasks.append((m,int(op),int(cut)))
print("events",len(chosen),"markets",len(tasks),flush=True)
os.makedirs("hist_trades",exist_ok=True)
def work(t):
    m,op,cut=t; fn=f"hist_trades/{m['ticker']}.json"
    if os.path.exists(fn): return
    tr=[];cur=None
    for _ in range(5):
        p={"ticker":m["ticker"],"min_ts":op,"max_ts":cut,"limit":1000}
        if cur:p["cursor"]=cur
        d=get("/historical/trades",p)
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
print("finished",done,flush=True)

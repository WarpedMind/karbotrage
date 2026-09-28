import os as _os; _D=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"..","cache","tape"); _os.makedirs(_D,exist_ok=True); _os.chdir(_D)
import requests, json, time, datetime as dt, os, glob, threading
from concurrent.futures import ThreadPoolExecutor
API="https://api.elections.kalshi.com/trade-api/v2"
CATS={"Climate and Weather","Commodities","Economics","Elections","Entertainment","Financials","Politics","Science and Technology"}
U=json.load(open("universe.json")); SER=U["series"]
iso=lambda t: int(dt.datetime.fromisoformat(t.replace("Z","+00:00")).timestamp())
local=threading.local()
def sess():
    if not hasattr(local,"s"):
        local.s=requests.Session(); local.s.headers["User-Agent"]="karbotrage-research/0.1"
    return local.s
_lock=threading.Lock(); _last=[0.0]
def _pace(min_gap=0.12):
    # global pacing across threads: <= ~8 req/s; the live-tier candle route 429s above that (Session 36)
    with _lock:
        now=time.time(); wait=_last[0]+min_gap-now
        if wait>0: time.sleep(wait)
        _last[0]=time.time()
def get(p,params):
    for i in range(20):
        _pace()
        try:
            r=sess().get(API+p,params=params,timeout=60)
            if r.status_code==200: return r.json()
            if r.status_code==404: return {"candlesticks":[],"_404":True}
        except requests.RequestException: pass
        time.sleep(min(1.0*(i+1),5))
    raise RuntimeError("fail "+p)
def _close(q):
    # historical tier: {"close": "0.14"}; live tier: {"close_dollars": "0.14"} -- CONFIRMED LIVE Session 36
    if not q: return None
    v=q.get("close", q.get("close_dollars"))
    return float(v) if v not in (None,"") else None
tasks=[]
for src,hist in [("hist_trades",True),("trades",False)]:
    for f in glob.glob(src+"/*.json"):
        d=json.load(open(f)); m=d["m"]; ser=m["event_ticker"].split("-")[0]
        if (SER.get(ser) or ["?"])[0] not in CATS: continue
        tasks.append((m,d["cutoff"],hist,ser))
print("markets",len(tasks),flush=True)
os.makedirs("candles",exist_ok=True)
def work(t):
    m,cut,hist,ser=t; fn=f"candles/{m['ticker']}.json"
    if os.path.exists(fn): return
    op=iso(m["open_time"]); out=[]; st=op
    # API caps number of candles per call; walk in 5000-hour chunks
    while st<cut:
        en=min(cut,st+3600*4800)
        p=f"/historical/markets/{m['ticker']}/candlesticks" if hist else f"/series/{ser}/markets/{m['ticker']}/candlesticks"
        d=get(p,{"start_ts":st,"end_ts":en,"period_interval":60})
        for c in d.get("candlesticks",[]):
            out.append([c["end_period_ts"],_close(c.get("yes_bid")),_close(c.get("yes_ask"))])
        st=en
    json.dump({"m":m,"cutoff":cut,"hist":hist,"candles":out},open(fn,"w"))
def safe(t):
    try: work(t)
    except RuntimeError as e:
        # recorded, never silently dropped: the analysis reports how many markets are missing
        with open("candles_failed.txt","a") as fh: fh.write(t[0]["ticker"]+"\n")
done=0
with ThreadPoolExecutor(6) as ex:
    for _ in ex.map(safe,tasks):
        done+=1
        if done%1000==0: print("done",done,flush=True)
print("finished",done,flush=True)

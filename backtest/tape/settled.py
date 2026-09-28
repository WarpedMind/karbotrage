import os as _os; _D=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"..","cache","tape"); _os.makedirs(_D,exist_ok=True); _os.chdir(_D)
import requests, json, time, datetime as dt, os
API="https://api.elections.kalshi.com/trade-api/v2"
s=requests.Session(); s.headers["User-Agent"]="karbotrage-research/0.1"
def get(p,params):
    for i in range(10):
        try:
            r=s.get(API+p,params=params,timeout=90)
            if r.status_code==200: return r.json()
        except requests.RequestException as e: pass
        time.sleep(2*(i+1))
    raise RuntimeError("fail")
keep=["ticker","event_ticker","open_time","close_time","occurrence_datetime","expected_expiration_time","settlement_ts","result","volume_fp","settlement_value_dollars","market_type","strike_type","title","yes_sub_title"]
day=dt.datetime(2026,7,30,tzinfo=dt.timezone.utc)
os.makedirs("settled_days",exist_ok=True)
tot=0; allm=0
while day<dt.datetime(2026,9,28,tzinfo=dt.timezone.utc):
    fn=f"settled_days/{day:%Y%m%d}.json"
    if not os.path.exists(fn):
        lo=int(day.timestamp()); hi=lo+86400-1
        out=[];cur=None;n=0
        while True:
            p={"limit":1000,"status":"settled","mve_filter":"exclude","min_close_ts":lo,"max_close_ts":hi}
            if cur:p["cursor"]=cur
            d=get("/markets",p); n+=1; allm+=len(d["markets"])
            for m in d["markets"]:
                try: v=float(m.get("volume_fp") or 0)
                except: v=0
                if v>=1000: out.append({k:m.get(k) for k in keep})
            cur=d.get("cursor")
            if not cur or not d["markets"]: break
        json.dump(out,open(fn,"w")); print(day.date(),"pages",n,"kept",len(out),flush=True)
    tot+=len(json.load(open(fn)))
    day+=dt.timedelta(days=1)
print("total kept",tot)

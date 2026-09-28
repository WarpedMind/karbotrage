import os as _os; _D=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"..","cache","tape"); _os.makedirs(_D,exist_ok=True); _os.chdir(_D)
import requests, json, time, collections
API="https://api.elections.kalshi.com/trade-api/v2"
s=requests.Session(); s.headers["User-Agent"]="karbotrage-research/0.1"
def get(p,params):
    for i in range(6):
        r=s.get(API+p,params=params,timeout=60)
        if r.status_code==200: return r.json()
        time.sleep(1.5*(i+1))
    raise RuntimeError(r.text[:200])
# series -> category
series={}
d=get("/series",{})
for x in d.get("series",[]): series[x["ticker"]]=(x.get("category"),x.get("title"))
print("series",len(series))
mk=[];cur=None
for _ in range(200):
    p={"limit":1000,"status":"open","mve_filter":"exclude"}
    if cur:p["cursor"]=cur
    d=get("/markets",p); mk+=d["markets"]; cur=d.get("cursor")
    if not cur: break
print("open markets",len(mk))
json.dump({"series":series,"markets":mk},open("universe.json","w"))

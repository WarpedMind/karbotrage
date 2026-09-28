import os as _os; _D=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"..","cache","tape"); _os.makedirs(_D,exist_ok=True); _os.chdir(_D)
import json, glob, collections, random, datetime as dt, math, sys
SRC=sys.argv[1] if len(sys.argv)>1 else "trades"; OUT=sys.argv[2] if len(sys.argv)>2 else "recs.json"
U=json.load(open("universe.json")); SER=U["series"]
MAKER_M1=set("""KXAAAGASM KXATPMATCH KXBALLONDOR KXBTCMAX150 KXCPI KXCPIYOY KXEGGS KXEMMYCACTO KXEMMYCACTR KXEMMYCSERIES KXEMMYDACTO KXEMMYDACTR KXEMMYDSERIES KXFED KXFEDDECISION KXGDP KXHEISMAN KXINXY KXIPO KXLALIGA KXLLM1 KXMARMAD KXMENWORLDCUP KXMLB KXMLBAL KXMLBASGAME KXMLBGAME KXMLBNL KXNASDAQ100Y KXNBA KXNBAEAST KXNBAMVP KXNBAROY KXNBAWEST KXNCAAF KXNCAAFACC KXNCAAFB10 KXNCAAFB12 KXNCAAFGAME KXNCAAFPLAYOFF KXNCAAFSEC KXNFLAFCCHAMP KXNFLAFCEAST KXNFLAFCNORTH KXNFLAFCSOUTH KXNFLAFCWEST KXNFLCOTY KXNFLCPOTY KXNFLDPOTY KXNFLDROTY KXNFLGAME KXNFLMVP KXNFLNFCCHAMP KXNFLNFCEAST KXNFLNFCNORTH KXNFLNFCSOUTH KXNFLNFCWEST KXNFLOPOTY KXNFLOROTY KXNHL KXNHLEAST KXNHLWEST KXPAYROLLS KXPGARYDER KXPGASOLHEIM KXPGATOUR KXRATECUTCOUNT KXSB KXSUPERBOWLHEADL KXU3 KXUCL KXUCLGAME KXWCGAME KXWNBA KXWNBAGAME KXWTAMATCH""".split())
ZERO=set("KXBTCY KXETHY KXCITRINI KXDOED KXGREENLAND KXIRANDEMOCRACY KXELECTIRAN KXGAMBLINGREPEAL KXLAYOFFSYINFO KXPAHLAVIHEAD".split())
rows=[]  # per market aggregates by band
BANDS=[(0.01,0.10),(0.10,0.30),(0.30,0.50),(0.50,0.70),(0.70,0.85),(0.85,0.99),(0.99,1.00)]
def band(p):
    for i,(a,b) in enumerate(BANDS):
        if a<=p<b: return i
    return None
def iso(t): return dt.datetime.fromisoformat(t.replace("Z","+00:00"))
recs=[]; trunc=0; nm=0
for f in glob.glob(SRC+"/*.json"):
    d=json.load(open(f)); m=d["m"]; nm+=1; trunc+=d["trunc"]
    ser=m["event_ticker"].split("-")[0]; cat=(SER.get(ser) or ["?"])[0]
    day=iso(m["settlement_ts"]).date(); yes=m["result"]=="yes"
    mt=0 if ser in ZERO else 1; mm=1 if ser in MAKER_M1 else 0
    agg=collections.defaultdict(lambda:[0.0,0.0,0.0])  # (role,band)->(profit,invested,contracts)
    for ts,c,yp,side in d["trades"]:
        p = yp if side=="yes" else 1-yp
        if p<=0 or p>=1: continue
        win = yes if side=="yes" else (not yes)
        ft=mt*0.07*p*(1-p); q=1-p; fm=mm*0.0175*q*(1-q)
        a=agg[("T",band(p))]; a[0]+=c*((1 if win else 0)-p-ft); a[1]+=c*(p+ft); a[2]+=c
        a=agg[("M",band(q))]; a[0]+=c*((0 if win else 1)-q-fm); a[1]+=c*(q+fm); a[2]+=c
    for (r,b),a in agg.items(): recs.append((m["ticker"],m["event_ticker"],ser,cat,day,r,b,*a))
print("markets",nm,"truncated",trunc,"records",len(recs))
json.dump([list(r[:4])+[str(r[4])]+list(r[5:]) for r in recs],open(OUT,"w"))

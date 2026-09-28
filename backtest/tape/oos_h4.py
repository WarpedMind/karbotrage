import os as _os; _D=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"..","cache","tape"); _os.makedirs(_D,exist_ok=True); _os.chdir(_D)
# H4 (frozen Session 36 from in-sample exploration, tested on the historical tier):
# equal-weight-per-market net taker return for buys at [0.70,0.85) in 8 non-sports categories.
import json
import stats
R=json.load(open("hist_recs.json")); stats.R=R
from stats import boot_eq, boot, fmt
CATS={"Climate and Weather","Commodities","Economics","Elections","Entertainment","Financials","Politics","Science and Technology"}
h4=[r for r in R if r[5]=="T" and r[6]==4 and r[3] in CATS]
days=sorted(set(r[4] for r in h4)); print("OOS dates",days[0],days[-1],len(days))
d=boot_eq(h4,B=4000); print(f"H4 PRIMARY eq-wt ret={100*d['ret']:+.2f}% CI[{100*d['lo']:+.2f},{100*d['hi']:+.2f}] one-sided P(<=0)={d['p_le0']:.4f} markets={d['n']}")
print(fmt("H4 dollar-wt",boot(h4,B=2000)))
t=len(days)//3
for i,s in enumerate([set(days[:t]),set(days[t:2*t]),set(days[2*t:])]):
    d=boot_eq([r for r in h4 if r[4] in s],B=1000); print(f"  period{i+1} {min(s)}..{max(s)} {100*d['ret']:+.2f}% CI[{100*d['lo']:+.2f},{100*d['hi']:+.2f}] P={d['p_le0']:.3f} n={d['n']}")
for c in sorted(CATS):
    sel=[r for r in h4 if r[3]==c]
    if len(set(x[0] for x in sel))<20: print(f"  {c:24s} n<20"); continue
    d=boot_eq(sel,B=500); print(f"  {c:24s} {100*d['ret']:+.2f}% CI[{100*d['lo']:+.2f},{100*d['hi']:+.2f}] n={d['n']}")
for role in "TM":
    for b in range(7):
        print(fmt(f"OOS {role} band{b}",boot([r for r in R if r[5]==role and r[6]==b],B=500)))

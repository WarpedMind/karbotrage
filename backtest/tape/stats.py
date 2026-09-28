import os as _os; _D=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"..","cache","tape"); _os.makedirs(_D,exist_ok=True); _os.chdir(_D)
import json, collections, random, sys
R=json.load(open("recs.json"))  # ticker,event,ser,cat,day,role,band,profit,invested,contracts
BANDS=["01-10c","10-30c","30-50c","50-70c","70-85c","85-99c","99c"]
def boot(sel, B=2000, key=lambda r:r[4], seed=7):
    """ratio-of-sums return, bootstrap resampling whole clusters (default: settlement date)"""
    cl=collections.defaultdict(lambda:[0.0,0.0,0.0]); mk=collections.defaultdict(set)
    for r in sel:
        c=cl[key(r)]; c[0]+=r[7]; c[1]+=r[8]; c[2]+=r[9]; mk[key(r)].add(r[0])
    ks=list(cl)
    if not ks: return None
    P=sum(cl[k][0] for k in ks); I=sum(cl[k][1] for k in ks)
    rnd=random.Random(seed); bs=[]
    for _ in range(B):
        p=i=0.0
        for _ in ks:
            c=cl[ks[rnd.randrange(len(ks))]]; p+=c[0]; i+=c[1]
        bs.append(p/i if i else 0)
    bs.sort()
    return dict(ret=P/I, lo=bs[int(.025*B)], hi=bs[int(.975*B)], p_le0=sum(1 for x in bs if x<=0)/B,
                clusters=len(ks), markets=len(set(r[0] for r in sel)), events=len(set(r[1] for r in sel)),
                contracts=sum(r[9] for r in sel), invested=I)
def fmt(name,d):
    if not d: return f"{name:34s} (no data)"
    return (f"{name:34s} ret={100*d['ret']:+6.2f}%  95%CI[{100*d['lo']:+6.2f},{100*d['hi']:+6.2f}]  "
            f"P(<=0)={d['p_le0']:.3f}  dates={d['clusters']} ev={d['events']} mkts={d['markets']} ctr={d['contracts']:,.0f} $inv={d['invested']:,.0f}")
def run(label, sel):
    print("\n=== "+label+" ===")
    for role in "TM":
        for b in range(7):
            print(fmt(f"{'taker' if role=='T' else 'maker'} buys @{BANDS[b]}", boot([r for r in sel if r[5]==role and r[6]==b])))
        print(fmt(f"{'taker' if role=='T' else 'maker'} ALL", boot([r for r in sel if r[5]==role])))
if __name__=="__main__":
    days=sorted(set(r[4] for r in R)); print("date range",days[0],days[-1],len(days))
    run("ALL, cluster=settlement date", R)
    # pre-registered
    print("\n=== PRE-REGISTERED (alpha=0.05/3=0.0167, one-sided) ===")
    print(fmt("H1 taker @85-99c", boot([r for r in R if r[5]=="T" and r[6]==5])))
    print(fmt("H2 taker @70-85c", boot([r for r in R if r[5]=="T" and r[6]==4])))
    print(fmt("H3 maker ALL", boot([r for r in R if r[5]=="M"])))
    print("  (event-clustered)")
    print(fmt("H1 taker @85-99c ev-cluster", boot([r for r in R if r[5]=="T" and r[6]==5],key=lambda r:r[1])))
    print(fmt("H2 taker @70-85c ev-cluster", boot([r for r in R if r[5]=="T" and r[6]==4],key=lambda r:r[1])))
    print(fmt("H3 maker ALL ev-cluster", boot([r for r in R if r[5]=="M"],key=lambda r:r[1])))
    # replication, thirds
    third=len(days)//3; per=[days[:third],days[third:2*third],days[2*third:]]
    print("\n=== REPLICATION by period ===")
    for i,ds in enumerate(per):
        s=set(ds); sub=[r for r in R if r[4] in s]
        print(f"-- period {i+1}: {ds[0]}..{ds[-1]}")
        print(fmt(" H1",boot([r for r in sub if r[5]=="T" and r[6]==5])))
        print(fmt(" H2",boot([r for r in sub if r[5]=="T" and r[6]==4])))
        print(fmt(" H3",boot([r for r in sub if r[5]=="M"])))
    # exploratory by category
    print("\n=== EXPLORATORY by category (NOT hypothesis tests) ===")
    cats=collections.Counter(r[3] for r in R)
    for c,_ in cats.most_common():
        sub=[r for r in R if r[3]==c]
        print(f"-- {c}")
        print(fmt("  taker @85-99c",boot([r for r in sub if r[5]=="T" and r[6]==5],B=500)))
        print(fmt("  taker @01-10c",boot([r for r in sub if r[5]=="T" and r[6]==0],B=500)))
        print(fmt("  taker ALL",boot([r for r in sub if r[5]=="T"],B=500)))
        print(fmt("  maker ALL",boot([r for r in sub if r[5]=="M"],B=500)))

def boot_eq(sel,B=2000,seed=11):
    """equal weight per market: mean of per-market returns, bootstrap by date"""
    per=collections.defaultdict(lambda:[0.0,0.0,None])
    for r in sel:
        p=per[r[0]]; p[0]+=r[7]; p[1]+=r[8]; p[2]=r[4]
    bydate=collections.defaultdict(list)
    for t,(pp,ii,d) in per.items():
        if ii>0: bydate[d].append(pp/ii)
    ks=list(bydate); allv=[v for k in ks for v in bydate[k]]
    rnd=random.Random(seed); bs=[]
    for _ in range(B):
        vs=[]
        for _ in ks: vs+=bydate[ks[rnd.randrange(len(ks))]]
        bs.append(sum(vs)/len(vs))
    bs.sort()
    return dict(ret=sum(allv)/len(allv),lo=bs[int(.025*B)],hi=bs[int(.975*B)],p_le0=sum(1 for x in bs if x<=0)/B,n=len(allv))
COVERED=("KXINX","KXNASDAQ100","KXBTC","KXETH","KXNBA","KXNHL","KXATP","KXWTA","KXMLB","KXNFL","KXPGATOUR","KXNCAA")
if __name__=="__main__":
    print("\n=== ROBUSTNESS: equal weight per market ===")
    for name,sel in [("H1",[r for r in R if r[5]=="T" and r[6]==5]),("H2",[r for r in R if r[5]=="T" and r[6]==4]),("H3",[r for r in R if r[5]=="M"])]:
        d=boot_eq(sel); print(f"{name} eq-wt ret={100*d['ret']:+.2f}% CI[{100*d['lo']:+.2f},{100*d['hi']:+.2f}] P(<=0)={d['p_le0']:.3f} n={d['n']}")
    print("\n=== EXPLORATORY: maker return, covered vs uncovered series ===")
    cov=lambda r: r[2].startswith(COVERED)
    print(fmt("maker, covered series",boot([r for r in R if r[5]=="M" and cov(r)])))
    print(fmt("maker, uncovered series",boot([r for r in R if r[5]=="M" and not cov(r)])))
    print(fmt("maker, uncovered, excl 01-10c",boot([r for r in R if r[5]=="M" and not cov(r) and r[6]!=0])))
    print(fmt("taker, uncovered series",boot([r for r in R if r[5]=="T" and not cov(r)])))

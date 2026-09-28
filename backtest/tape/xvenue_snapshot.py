"""One-shot live snapshot: Kalshi KXNFLGAME vs Polymarket NFL moneylines (both public, no auth).

Session 36 result (2026-09-28, 11 games for 2026-10-04): median |mid difference| 0.5c,
max 1.5c, and no crossed quotes between venues -- the two venues are already tied
together on major-league sports. Read-only; places nothing.
"""
import json, statistics, requests
API = "https://api.elections.kalshi.com/trade-api/v2"
MONTHS = ["", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
NAMES = {"Cardinals": "ARI", "Falcons": "ATL", "Ravens": "BAL", "Bills": "BUF", "Panthers": "CAR", "Bears": "CHI",
         "Bengals": "CIN", "Browns": "CLE", "Cowboys": "DAL", "Broncos": "DEN", "Lions": "DET", "Packers": "GB",
         "Texans": "HOU", "Colts": "IND", "Jaguars": "JAC", "Chiefs": "KC", "Raiders": "LV", "Chargers": "LAC",
         "Rams": "LA", "Dolphins": "MIA", "Vikings": "MIN", "Patriots": "NE", "Saints": "NO", "Giants": "NYG",
         "Jets": "NYJ", "Eagles": "PHI", "Steelers": "PIT", "49ers": "SF", "Seahawks": "SEA", "Buccaneers": "TB",
         "Titans": "TEN", "Commanders": "WAS"}

def main():
    k = requests.get(API + "/markets", params={"series_ticker": "KXNFLGAME", "status": "open", "limit": 200}, timeout=30).json()["markets"]
    pm = requests.get("https://gamma-api.polymarket.com/events", params={"closed": "false", "limit": 100, "tag_slug": "nfl",
                      "order": "volume24hr", "ascending": "false"}, timeout=30).json()
    rows = []
    for e in pm:
        if not e["slug"].startswith("nfl-") or "props" in e["slug"]:
            continue
        mm = [m for m in e["markets"] if " vs. " in m["question"] and m.get("outcomes", "").count(",") == 1]
        if not mm or mm[0].get("bestBid") is None or mm[0].get("bestAsk") is None:
            continue
        m = mm[0]; team = json.loads(m["outcomes"])[0]; ab = NAMES.get(team); date = e["slug"][-10:]
        tag = "26" + MONTHS[int(date[5:7])] + date[8:10]
        hit = [x for x in k if x["ticker"].endswith("-" + str(ab)) and x["event_ticker"].split("-")[1][:7] == tag]
        if not hit:
            continue
        kb, ka = float(hit[0]["yes_bid_dollars"]), float(hit[0]["yes_ask_dollars"])
        rows.append((e["slug"], team, m["bestBid"], m["bestAsk"], kb, ka))
    for r in rows:
        print(f"{r[0]:28s} {r[1]:11s} PM {r[2]:.3f}/{r[3]:.3f}  K {r[4]:.2f}/{r[5]:.2f}  "
              f"mid diff {(r[4]+r[5])/2-(r[2]+r[3])/2:+.3f} cross {max(r[4]-r[3], r[2]-r[5]):+.3f}")
    d = [abs((r[4]+r[5])/2-(r[2]+r[3])/2) for r in rows]
    if d:
        print("n", len(d), "median |mid diff|", round(statistics.median(d), 4), "max", round(max(d), 4))

if __name__ == "__main__":
    main()

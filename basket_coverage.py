from datetime import timedelta
import math
import basket_telegram_outcomes as outcomes

def band(value):
    try: v=float(value)
    except (ValueError,TypeError): return "unknown"
    if not math.isfinite(v) or v<=1: return "unknown"
    return next((label for limit,label in [(1.2,"1.01-1.19"),(1.4,"1.20-1.39"),(1.6,"1.40-1.59"),(1.8,"1.60-1.79"),(2,"1.80-1.99")] if v<limit),"2.00+")

def observe(state,match,now,quarter,favorite=None,source=None,check_shadow=True):
    if match.get("status_code")!="live" or not match.get("code"): return
    row=state.setdefault("coverage",{}).setdefault(str(match["code"]),{})
    row.update(league=match.get("league_name") or "unknown",last=now.isoformat())
    if favorite in ("home","away"):
        row["favorite"]=True
        if source: row["source"]=source
    if (match.get("freshness") or {}).get("stale") is True or quarter not in (1,2,3,4): return
    scores=[(match.get(k) or {}).get("score") for k in ("home_team","away_team")]
    if not all(str(x).isdigit() for x in scores): return
    row["valid"]=True
    if check_shadow and favorite not in ("home","away") and int(scores[0])!=int(scores[1]):
        quarters=set(row.get("hypothetical_quarters",[]))
        quarters.add(quarter)
        row["hypothetical_quarters"]=sorted(quarters)

def report(state,now,parse):
    rows=state.setdefault("coverage",{})
    for k in list(rows):
        last=parse(rows[k].get("last"))
        if not last or now-last>timedelta(days=30): del rows[k]
    groups={}
    providers={}
    for row in rows.values():
        g=groups.setdefault(row["league"],dict(matches=0,valid=0,favorites=0,hypothetical=0))
        g["matches"]+=1
        g["valid"]+=bool(row.get("valid"))
        g["favorites"]+=bool(row.get("favorite"))
        g["hypothetical"]+=bool(row.get("hypothetical_quarters"))
        if row.get("favorite"):
            src=row.get("source","unknown")
            providers[src]=providers.get(src,0)+1
    alerts={k:dict(v,provider=v.get("provider") or "unknown",odds_band=band(v.get("favorite_odds"))) for k,v in state.get("telegram_alerts",{}).items() if v.get("message_id")}
    def grouped(field):
        result={}
        for value in sorted({str(v.get(field) or "unknown") for v in alerts.values()}):
            result[value]=outcomes.report({"telegram_alerts":{k:v for k,v in alerts.items() if str(v.get(field) or "unknown")==value}})["total"]
        return result
    return dict(coverage_30d=groups,favorites_by_provider_30d=providers,alerts_by_league=grouped("league"),alerts_by_provider=grouped("provider"),alerts_by_odds_band=grouped("odds_band"))

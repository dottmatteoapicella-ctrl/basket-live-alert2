"""Conservative MoviOdds fallback; disabled until shared quota is confirmed."""
import math
from datetime import timedelta
import requests

LEAGUES={
    "greeceeliteleague":"greeceeliteleague",
    "turkeytb2l":"turkiyetb2l",
    "asiaasiacupqualificationprequalification2":"fibaasiacupprequalifiers",
}
ALIASES={"anagennisi":"asanagennisipetralonon","kronosagioudimitriou":"kronosagdimitriou",
         "komotini":"gaskomotini","koroivos":"asakoroivos","bayrampasa":"bayrampasabelediyespor"}

def collect(payload,now,parse,key):
    out={}
    for m in payload.get("matches",[]):
        if not isinstance(m,dict) or m.get("in_play") is not False or m.get("stale") is True: continue
        league=key(m.get("league"))
        if not league or any(x in league for x in ("ebasket","virtual","esport","3x3")): continue
        start,polled=parse(m.get("kickoff_utc")),parse(m.get("polled_at"))
        if not start or start<=now+timedelta(minutes=5) or not polled or not 0<=(now-polled).total_seconds()<=900: continue
        names=[key(m.get(k)) for k in ("home","away")]
        if not all(names) or names[0]==names[1]: continue
        prices={}
        for q in m.get("quotes") or []:
            if not isinstance(q,dict) or q.get("group_name")!="Game" or q.get("line")!="To Win" or q.get("suspended") is True or str(q.get("phase","")).lower() in ("live","inplay","in_play"): continue
            ts=parse(q.get("ts"))
            side=key(q.get("side"))
            if not ts or not 0<=(now-ts).total_seconds()<=7200 or ts>=start or side not in names: continue
            try: price=float(q.get("odds_dec"))
            except (TypeError,ValueError): continue
            if not math.isfinite(price) or price<=1: continue
            if side in prices and prices[side]!=price: prices={}; break
            prices[side]=price
        if len(prices)!=2 or prices[names[0]]==prices[names[1]]: continue
        if m.get("id") is None: continue
        out[str(m["id"])]=dict(league=league,names=names,prices=[prices[n] for n in names],start=start.isoformat(),captured=now.isoformat())
    return out

def favorite(match,state,parse,key):
    start=parse(match.get("start_at"))
    league=key(match.get("league_name"))
    names=[key((match.get(k) or {}).get("name")) for k in ("home_team","away_team")]
    if not start or not all(names) or names[0]==names[1]: return None
    target=LEAGUES.get(league,league)
    def alias(n): return ALIASES.get(n,n) if target in LEAGUES.values() else n
    names=list(map(alias,names))
    candidates=[]
    for r in state.get("moviodds_games",{}).values():
        kickoff,captured=parse(r["start"]),parse(r["captured"])
        if r["league"]!=target or not kickoff or not captured or captured>=min(start,kickoff) or abs((start-kickoff).total_seconds())>5400: continue
        rn=list(map(alias,r["names"]))
        if rn==names: prices=r["prices"]
        elif rn==names[::-1]: prices=r["prices"][::-1]
        else: continue
        candidates.append(prices)
    if len(candidates)!=1: return None
    h,a=candidates[0]
    return {"favorite":"home" if h<a else "away","prices":{"home":h,"away":a},"source":"moviodds"}

def refresh(state,now,parse,key,api_key,enabled,save):
    if not enabled or not api_key: return
    budget=state.setdefault("moviodds_budget",{})
    day=now.date().isoformat()
    if budget.get("day")!=day: budget.clear(); budget.update(day=day,requests=0)
    last=parse(state.get("moviodds_last"))
    if budget.get("blocked") or budget["requests"]>=8 or (last and now-last<timedelta(hours=3)): return
    budget["requests"]+=1
    state["moviodds_last"]=now.isoformat()
    save(state)
    try:
        res=requests.get("https://moviodds.com/odds",params={"sport":"basketball"},headers={"X-API-Key":api_key,"Accept":"application/json"},timeout=25,allow_redirects=False)
        if res.status_code!=200:
            if res.status_code in (401,403,429): budget["blocked"]=True
            print("MoviOdds HTTP",res.status_code); save(state); return
        records=collect(res.json(),now,parse,key)
        cache=state.setdefault("moviodds_games",{})
        cache.update(records)
        state["moviodds_games"]={k:v for k,v in cache.items() if parse(v["start"]) and now-parse(v["start"])<timedelta(days=3)}
        print("MoviOdds valid prematch",len(records),"budget",budget["requests"])
    except (requests.RequestException,ValueError,TypeError): print("MoviOdds unavailable")
    save(state)

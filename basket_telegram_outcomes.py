"""Update original basket Telegram alerts only after a verified final score."""
import requests

FINISHED = {"finished", "completed", "complete", "final", "ft", "ended", "full_time", "full-time"}

def verified_final(match):
    if not isinstance(match, dict) or (match.get("freshness") or {}).get("stale") is True:
        return None
    status = str(match.get("status_code") or "").strip().lower()
    if status not in FINISHED:
        return None
    home = match.get("home_team") or {}
    away = match.get("away_team") or {}
    try:
        scores = [int(home["score"]), int(away["score"])]
    except (ValueError, TypeError, KeyError):
        return None
    if min(scores) < 0 or scores[0] == scores[1]:
        return None
    return scores

def update(state, matches, token, chat_id):
    by_code = {str(m["code"]): m for m in matches if isinstance(m, dict) and m.get("code") is not None}
    updated = 0
    counts = dict(pending=0, missing_code=0, teams_mismatch=0, invalid_final=0, invalid_favorite=0, edit_failed=0)
    print("Diagnostica basket risultati ricevuti:", len(matches))
    for record in state.get("telegram_alerts", {}).values():
        if record.get("finalized") or not record.get("message_id"):
            continue
        counts["pending"] += 1
        match = by_code.get(str(record.get("game_code")))
        if not match:
            counts["missing_code"] += 1
            continue
        home = (match.get("home_team") or {}).get("name")
        away = (match.get("away_team") or {}).get("name")
        if [home, away] != record.get("teams"):
            counts["teams_mismatch"] += 1
            continue
        scores = verified_final(match)
        if scores is None:
            counts["invalid_final"] += 1
            continue
        favorite = record.get("favorite")
        if favorite not in ("home", "away"):
            counts["invalid_favorite"] += 1
            continue
        favorite_lost = scores[0] < scores[1] if favorite == "home" else scores[1] < scores[0]
        marker = "✅ FAVORITA SCONFITTA" if favorite_lost else "❌ FAVORITA VINCENTE"
        winner = home if scores[0] > scores[1] else away
        body = (record["text"] + "\n\n" + marker +
                f"\n🏁 RISULTATO FINALE: {scores[0]}-{scores[1]}" +
                f"\n🏆 Vincitrice: {winner}")
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                data={"chat_id": chat_id, "message_id": record["message_id"], "text": body},
                timeout=25,
            )
            response.raise_for_status()
            if not response.json().get("ok"):
                raise ValueError("Telegram edit not confirmed")
        except (requests.RequestException, ValueError) as exc:
            counts["edit_failed"] += 1
            print("Modifica Telegram basket fallita:", type(exc).__name__)
            continue
        record["finalized"] = True
        record["marker"] = marker
        record["final_score"] = scores
        from datetime import datetime, timezone
        record["verified_at"] = datetime.now(timezone.utc).isoformat()
        updated += 1
    for key, value in counts.items():
        print("Diagnostica basket", key + ":", value)
    print("Alert basket aggiornati con finale:", updated)
    return updated


def report(state):
    rows = list(state.get("telegram_alerts", {}).values())
    def summarize(items):
        wins = sum(r.get("finalized") is True and str(r.get("marker","")).startswith("✅") for r in items)
        losses = sum(r.get("finalized") is True and str(r.get("marker","")).startswith("❌") for r in items)
        verified = wins + losses
        return dict(alerts=len(items), verified=verified, favorite_lost=wins,
                    favorite_won=losses, pending=sum(not r.get("finalized") for r in items),
                    unknown=len(items)-verified-sum(not r.get("finalized") for r in items),
                    success_pct=round(100*wins/verified,2) if verified else None)
    def grouped(key):
        groups = {}
        for r in rows:
            groups.setdefault(str(r.get(key) if r.get(key) is not None else "unknown"), []).append(r)
        return {k:summarize(v) for k,v in groups.items()}
    return dict(total=summarize(rows), by_league=grouped("league"), by_quarter=grouped("quarter"),
                by_deficit=grouped("deficit"), by_favorite_odds=grouped("favorite_odds"))


def q3_score(match):
    # Only explicit individual period scores; never substitute current totals.
    scores = []
    for side in ("home_team", "away_team"):
        team = match.get(side) or {}
        values = [team.get("period"+str(i)) for i in (1,2,3)]
        if any(isinstance(v,bool) or not str(v).isdigit() for v in values):
            return None
        total = sum(int(v) for v in values)
        if not str(team.get("score")).isdigit() or total > int(team["score"]):
            return None
        scores.append(total)
    return scores


def observe_q3(state, match, quarter, favorite, now):
    if quarter != 4 or favorite not in ("home","away") or not match.get("code"):
        return
    if (match.get("freshness") or {}).get("stale") is True:
        return
    scores = q3_score(match)
    if scores is None:
        print("SHADOW_Q3 missing explicit period scores")
        return
    index = 0 if favorite == "home" else 1
    if scores[index] >= scores[1-index]:
        return
    teams = [(match.get(k) or {}).get("name") for k in ("home_team","away_team")]
    if not all(teams):
        return
    state.setdefault("shadow_q3", {}).setdefault(str(match["code"]), {
        "game_code":str(match["code"]), "teams":teams, "favorite":favorite,
        "q3_score":scores, "created_at":now.isoformat(), "finalized":False})


def settle_q3(state, matches):
    for match in matches:
        if not isinstance(match,dict):
            continue
        row = state.get("shadow_q3",{}).get(str(match.get("code")))
        if not row or row.get("finalized"):
            continue
        teams = [(match.get(k) or {}).get("name") for k in ("home_team","away_team")]
        scores = verified_final(match)
        if scores is None or teams != row["teams"]:
            continue
        index = 0 if row["favorite"] == "home" else 1
        row.update(finalized=True, final_score=scores,
                   favorite_lost=scores[index]<scores[1-index])


def shadow_report(state):
    rows = list(state.get("shadow_q3",{}).values())
    verified = [r for r in rows if r.get("finalized")]
    lost = sum(r.get("favorite_lost") is True for r in verified)
    return dict(alerts=len(rows), verified=len(verified), pending=len(rows)-len(verified),
                favorite_lost=lost, favorite_won=len(verified)-lost,
                success_pct=round(100*lost/len(verified),2) if verified else None)


def alert_quarter_allowed(quarter):
    return type(quarter) is int and quarter in (3, 4)

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
        updated += 1
    for key, value in counts.items():
        print("Diagnostica basket", key + ":", value)
    print("Alert basket aggiornati con finale:", updated)
    return updated

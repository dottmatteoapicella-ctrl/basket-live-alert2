
import os
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

API_KEY = os.environ["BASKET_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BASE_URL = "https://v1.basketball.api-sports.io"
STATE_FILE = Path("basket_state.json")

MAX_REQUESTS_PER_RUN = 3
MIN_REQUESTS_REMAINING = 10

# Cerca le quote delle partite che iniziano
# entro le prossime 3 ore.
UPCOMING_WINDOW_MINUTES = 180

requests_used = 0
requests_remaining = None


def api_get(endpoint, params):
    global requests_used, requests_remaining

    if requests_used >= MAX_REQUESTS_PER_RUN:
        print("DEBUG API: limite per esecuzione raggiunto.")
        return None

    if (
        requests_remaining is not None
        and requests_remaining <= MIN_REQUESTS_REMAINING
    ):
        print("DEBUG API: limite giornaliero di sicurezza.")
        return None

    response = requests.get(
        f"{BASE_URL}/{endpoint}",
        headers={"x-apisports-key": API_KEY},
        params=params,
        timeout=20,
    )
    response.raise_for_status()
    requests_used += 1

    remaining = response.headers.get(
        "x-ratelimit-requests-remaining"
    )

    if remaining is not None:
        try:
            requests_remaining = int(remaining)
        except ValueError:
            print("DEBUG API: limite non interpretabile.")

    data = response.json()

    if data.get("errors"):
        print("Errore API:", data["errors"])
        return None

    return data.get("response", [])


def send_telegram(message):
    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
        },
        timeout=20,
    )
    response.raise_for_status()

    if not response.json().get("ok"):
        raise RuntimeError("Telegram non ha confermato l'invio.")


def load_state():
    if STATE_FILE.exists():
        print("DEBUG STATO: file trovato.")
        return json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

    print("DEBUG STATO: file non trovato.")
    return {
        "favorites": {},
        "alerts": [],
        "attempted": {},
    }


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, indent=2),
        encoding="utf-8",
    )


def get_favorite(game_id):
    print("DEBUG QUOTE: richiesta", game_id)

    odds_data = api_get("odds", {"game": game_id})

    if not odds_data:
        print("DEBUG QUOTE: nessuna quota disponibile.")
        return None

    for result in odds_data:
        for bookmaker in result.get("bookmakers", []):
            for bet in bookmaker.get("bets", []):
                if bet.get("name") != "Home/Away":
                    continue

                values = {}

                for item in bet.get("values", []):
                    if item.get("value") in ("Home", "Away"):
                        try:
                            values[item["value"]] = float(
                                item["odd"]
                            )
                        except (ValueError, TypeError, KeyError):
                            pass

                if "Home" in values and "Away" in values:
                    print("DEBUG QUOTE:", values)

                    if values["Home"] == values["Away"]:
                        return None

                    return (
                        "home"
                        if values["Home"] < values["Away"]
                        else "away"
                    )

    print("DEBUG QUOTE: mercato Home/Away assente.")
    return None


def main():
    print("=== BASKET LIVE ALERT ===")

    state = load_state()
    state.setdefault("favorites", {})
    state.setdefault("alerts", [])
    state.setdefault("attempted", {})

    print("Favorite salvate:", len(state["favorites"]))
    print("Alert gia inviati:", len(state["alerts"]))
    print("Tentativi salvati:", len(state["attempted"]))

    now = datetime.now(timezone.utc)
    today = now.date()

    games = api_get(
        "games",
        {"date": today.isoformat()},
    )

    if games is None:
        print("Impossibile recuperare le partite.")
        return

    print("Data controllata:", today)
    print("Partite trovate:", len(games))

    cutoff = (now - timedelta(days=1)).isoformat()

    state["attempted"] = {
        game_id: timestamp
        for game_id, timestamp in state["attempted"].items()
        if timestamp >= cutoff
    }

    live_statuses = ("Q1", "Q2", "Q3", "Q4", "HT", "OT")

    live_count = 0
    live_with_favorite = 0
    live_without_favorite = 0
    losing_count = 0
    alerts_sent = 0

    print("\n=== DEBUG PARTITE LIVE ===")

    for game in games:
        game_id = str(game["id"])
        status = game["status"]["short"]

        if status not in live_statuses:
            continue

        live_count += 1

        home = game["teams"]["home"]["name"]
        away = game["teams"]["away"]["name"]

        home_score = game["scores"]["home"]["total"]
        away_score = game["scores"]["away"]["total"]

        favorite = state["favorites"].get(game_id)

        print(
            f"DEBUG LIVE: {home} vs {away}"
            f" | ID={game_id}"
            f" | stato={status}"
            f" | risultato={home_score}-{away_score}"
            f" | favorita={favorite}"
        )

        if not favorite:
            live_without_favorite += 1
            print("DEBUG: favorita non conosciuta.")
            continue

        live_with_favorite += 1

        if game_id in state["alerts"]:
            print("DEBUG: alert gia inviato.")
            continue

        if home_score is None or away_score is None:
            print("DEBUG: punteggio mancante.")
            continue

        losing = (
            favorite == "home"
            and home_score < away_score
        ) or (
            favorite == "away"
            and away_score < home_score
        )

        if not losing:
            print("DEBUG: favorita non in svantaggio.")
            continue

        losing_count += 1

        favorite_name = (
            home if favorite == "home" else away
        )

        message = (
            "🏀 BASKET - FAVORITA IN SVANTAGGIO!\n\n"
            f"{home} {home_score} - {away_score} {away}\n"
            f"Favorita pre-match: {favorite_name}\n"
            f"Periodo: {status}"
        )

        send_telegram(message)

        state["alerts"].append(game_id)
        save_state(state)

        alerts_sent += 1
        print("ALERT INVIATO:", game_id)

    print("\n=== DEBUG PARTITE DA INIZIARE ===")

    upcoming = []
    window_end = now + timedelta(
        minutes=UPCOMING_WINDOW_MINUTES
    )

    for game in games:
        game_id = str(game["id"])

        if game["status"]["short"] != "NS":
            continue

        if game_id in state["favorites"]:
            continue

        if game_id in state["attempted"]:
            continue

        timestamp = game.get("timestamp")

        if timestamp is None:
            continue

        try:
            start_time = datetime.fromtimestamp(
                int(timestamp),
                tz=timezone.utc
            )
        except (ValueError, TypeError, OverflowError):
            continue

        # Solo partite future entro le prossime 3 ore.
        if not (now <= start_time <= window_end):
            continue

        upcoming.append(game)

    upcoming.sort(
        key=lambda game: int(game["timestamp"])
    )

    print(
        "Partite candidate entro 3 ore:",
        len(upcoming)
    )

    for game in upcoming:
        if requests_used >= MAX_REQUESTS_PER_RUN:
            print("DEBUG: limite richieste raggiunto.")
            break

        if (
            requests_remaining is not None
            and requests_remaining <= MIN_REQUESTS_REMAINING
        ):
            print("DEBUG: limite sicurezza API.")
            break

        game_id = str(game["id"])

        favorite = get_favorite(game_id)

        state["attempted"][game_id] = now.isoformat()

        if favorite:
            state["favorites"][game_id] = favorite

            print(
                "Favorita salvata:",
                game_id,
                favorite
            )
        else:
            print(
                "DEBUG: favorita non disponibile:",
                game_id
            )

    save_state(state)

    print("\n=== RIEPILOGO DEBUG BASKET ===")
    print("Partite totali:", len(games))
    print("Partite LIVE:", live_count)
    print("LIVE con favorita:", live_with_favorite)
    print("LIVE senza favorita:", live_without_favorite)
    print("Favorite in svantaggio:", losing_count)
    print("Nuovi alert inviati:", alerts_sent)
    print("Favorite salvate:", len(state["favorites"]))
    print("Richieste API utilizzate:", requests_used)
    print("Richieste API rimaste:", requests_remaining)


if __name__ == "__main__":
    main()

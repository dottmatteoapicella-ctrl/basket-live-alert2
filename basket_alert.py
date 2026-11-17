import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SPORTSTATS_API_KEY = os.environ["SPORTSTATS_API_KEY"]
ODDS_API_KEY = os.environ["ODDS_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = Path("basket_state.json")

# Una richiesta live ogni 20 minuti = circa 72 richieste SportStats al giorno.
# The Odds API: 5 campionati x 1 aggiornamento al giorno = circa 150 crediti/mese.
ODDS_SPORTS = (
    "basketball_nba",
    "basketball_euroleague",
    "basketball_nbl",
    "basketball_ncaab",
    "basketball_wncaab",
)
ODDS_REFRESH_HOURS = 24
MAX_START_DIFFERENCE_HOURS = 6


def utc_now():
    return datetime.now(timezone.utc)


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def normalize(name):
    name = unicodedata.normalize("NFKD", name or "")
    name = "".join(c for c in name if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def load_state():
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    else:
        state = {}
    state.setdefault("favorites", {})
    state.setdefault("alerts", [])
    state.setdefault("attempted", {})
    state.setdefault("odds_games", {})
    state.setdefault("odds_refreshed", {})
    return state


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def fetch_odds(sport):
    response = requests.get(
        f"https://api.the-odds-api.com/v4/sports/{sport}/odds/",
        params={
            "apiKey": ODDS_API_KEY,
            "regions": "eu",
            "markets": "h2h",
            "oddsFormat": "decimal",
        },
        timeout=25,
    )
    print("ODDS:", sport, "HTTP", response.status_code,
          "crediti rimasti:", response.headers.get("x-requests-remaining"))
    response.raise_for_status()
    return response.json()


def update_odds(state, now):
    for sport in ODDS_SPORTS:
        last = parse_time(state["odds_refreshed"].get(sport))
        if last and now - last < timedelta(hours=ODDS_REFRESH_HOURS):
            continue
        try:
            games = fetch_odds(sport)
        except (requests.RequestException, ValueError) as exc:
            print("ERRORE QUOTE:", sport, str(exc))
            continue

        count = 0
        for game in games:
            start = parse_time(game.get("commence_time"))
            # Mai considerare quote recuperate a partita iniziata come pre-match.
            if not start or start <= now + timedelta(minutes=5):
                continue
            home = game.get("home_team", "")
            away = game.get("away_team", "")
            if not home or not away:
                continue
            favorite = None
            for bookmaker in game.get("bookmakers", []):
                market = next((m for m in bookmaker.get("markets", [])
                               if m.get("key") == "h2h"), None)
                if not market:
                    continue
                prices = {}
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") in (home, away):
                        try:
                            prices[outcome["name"]] = float(outcome["price"])
                        except (ValueError, TypeError, KeyError):
                            pass
                if home in prices and away in prices and prices[home] != prices[away]:
                    favorite = "home" if prices[home] < prices[away] else "away"
                    break
            if not favorite:
                continue
            event_id = str(game.get("id") or f"{sport}:{start.isoformat()}:{home}:{away}")
            state["odds_games"][event_id] = {
                "sport": sport,
                "home": home,
                "away": away,
                "start": start.isoformat(),
                "favorite": favorite,
                "captured_at": now.isoformat(),
            }
            count += 1
        state["odds_refreshed"][sport] = now.isoformat()
        save_state(state)
        print("Favorite pre-match memorizzate:", sport, count)

    # Elimina soltanto la cache delle quote vecchie; non toccare lo storico API-Sports.
    cutoff = now - timedelta(days=3)
    state["odds_games"] = {
        key: value for key, value in state["odds_games"].items()
        if (parse_time(value.get("start")) or cutoff - timedelta(seconds=1)) >= cutoff
    }


def fetch_live():
    response = requests.get(
        "https://api.sportstatsapi.com/api/v1/basketball/matches/live",
        headers={"Authorization": f"Bearer {SPORTSTATS_API_KEY}"},
        timeout=25,
    )
    print("SPORTSTATS HTTP:", response.status_code)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success", False):
        raise RuntimeError("SportStatsAPI non ha confermato la risposta")
    return payload.get("data", [])


def find_prematch_favorite(match, state):
    home = normalize((match.get("home_team") or {}).get("name"))
    away = normalize((match.get("away_team") or {}).get("name"))
    start = parse_time(match.get("start_at"))
    if not home or not away or not start:
        return None
    candidates = []
    for item in state["odds_games"].values():
        odds_start = parse_time(item.get("start"))
        if not odds_start or abs((start - odds_start).total_seconds()) > MAX_START_DIFFERENCE_HOURS * 3600:
            continue
        if home == normalize(item.get("home")) and away == normalize(item.get("away")):
            candidates.append((abs((start - odds_start).total_seconds()), item))
    if not candidates:
        return None
    return min(candidates, key=lambda pair: pair[0])[1]["favorite"]


def send_telegram(message):
    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
        timeout=25,
    )
    response.raise_for_status()
    if not response.json().get("ok"):
        raise RuntimeError("Telegram non ha confermato l'invio")


def main():
    print("=== BASKET LIVE ALERT / SPORTSTATS + ODDS ===")
    state = load_state()
    now = utc_now()
    update_odds(state, now)
    save_state(state)

    try:
        matches = fetch_live()
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        print("ERRORE SPORTSTATS:", str(exc))
        return
    print("Partite live:", len(matches))
    sent = 0
    skipped_stale = 0
    with_favorite = 0

    for match in matches:
        if match.get("status_code") != "live":
            continue
        if (match.get("freshness") or {}).get("stale") is True:
            skipped_stale += 1
            continue
        home_data = match.get("home_team") or {}
        away_data = match.get("away_team") or {}
        home = home_data.get("name", "")
        away = away_data.get("name", "")
        home_score = home_data.get("score")
        away_score = away_data.get("score")
        if not home or not away or home_score is None or away_score is None:
            continue
        game_code = match.get("code")
        if not game_code:
            continue
        game_id = f"sportstats:{game_code}"
        if game_id in state["alerts"]:
            continue

        favorite = state["favorites"].get(game_id)
        if favorite not in ("home", "away"):
            favorite = find_prematch_favorite(match, state)
            if favorite:
                state["favorites"][game_id] = favorite
                save_state(state)
        if not favorite:
            continue
        with_favorite += 1
        try:
            home_score = int(home_score)
            away_score = int(away_score)
        except (ValueError, TypeError):
            continue
        losing = ((favorite == "home" and home_score < away_score) or
                  (favorite == "away" and away_score < home_score))
        if not losing:
            continue

        favorite_name = home if favorite == "home" else away
        message = (
            "🚨🏀 FAVORITA IN SVANTAGGIO!\n\n"
            f"🏆 Campionato: {match.get('league_name') or 'Non disponibile'}\n\n"
            f"🏠 {home}\n"
            f"✈️ {away}\n\n"
            f"📊 Risultato: {home_score} - {away_score}\n"
            "⏱ Periodo: partita in corso\n\n"
            f"⭐ Favorita pre-match: {favorite_name}\n"
            f"📉 Svantaggio: {abs(home_score - away_score)} punti\n\n"
            "Dati: Stadiro / SportStatsAPI (sportstatsapi.com)"
        )
        send_telegram(message)
        state["alerts"].append(game_id)
        save_state(state)
        sent += 1
        print("ALERT INVIATO:", game_id)

    save_state(state)
    print("Live con favorita nota:", with_favorite)
    print("Live ignorate per dati stale:", skipped_stale)
    print("Nuovi alert:", sent)
    print("Quote pre-match in cache:", len(state["odds_games"]))


if __name__ == "__main__":
    main()

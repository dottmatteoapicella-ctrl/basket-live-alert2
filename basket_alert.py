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
# EuroLeague: seconda acquisizione giornaliera per ridurre i match persi.
EUROLEAGUE_REFRESH_HOURS = 12
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


TEAM_ALIASES = {
    # Nomi brevi SportStats e denominazioni ufficiali nei mercati quote.
    "bayern": "bayernmunich",
    "fcbayernmuenchen": "bayernmunich",
    "fcbayernmunich": "bayernmunich",
    "bayernmuenchen": "bayernmunich",
    "bayernmunich": "bayernmunich",
    "baskonia": "baskonia",
    "saski baskonia": "baskonia",
    "saskibaskonia": "baskonia",
    "baskoniavitoriagasteiz": "baskonia",
}


def team_key(name):
    """Normalizza solo varianti editoriali sicure, senza indovinare squadre."""
    value = unicodedata.normalize("NFKD", name or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.casefold().replace("&", " and ")
    # Abbreviazioni editoriali frequenti, senza rimuovere citta' o nomi.
    value = re.sub(r"\\b(?:basketball|basket|bc|bk|b c)\\b", " ", value)
    key = re.sub(r"[^a-z0-9]", "", value)
    return TEAM_ALIASES.get(key, key)


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
        refresh_hours = EUROLEAGUE_REFRESH_HOURS if sport == "basketball_euroleague" else ODDS_REFRESH_HOURS
        if last and now - last < timedelta(hours=refresh_hours):
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
        odds_home, odds_away = normalize(item.get("home")), normalize(item.get("away"))
        difference = abs((start - odds_start).total_seconds())
        exact = home == odds_home and away == odds_away
        reversed_exact = home == odds_away and away == odds_home
        # Varianti innocue come "BC" / "Basketball": solo se entrambe le squadre
        # coincidono e l'orario e' entro 90 minuti.
        loose = (difference <= 90 * 60 and
                 team_key((match.get("home_team") or {}).get("name")) == team_key(item.get("home")) and
                 team_key((match.get("away_team") or {}).get("name")) == team_key(item.get("away")))
        reversed_loose = (difference <= 90 * 60 and
                          team_key((match.get("home_team") or {}).get("name")) == team_key(item.get("away")) and
                          team_key((match.get("away_team") or {}).get("name")) == team_key(item.get("home")))
        if exact or loose:
            candidates.append((0 if exact else 1, difference, item["favorite"]))
        elif reversed_exact or reversed_loose:
            inverted = "away" if item["favorite"] == "home" else "home"
            candidates.append((0 if reversed_exact else 1, difference, inverted))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: (pair[0], pair[1]))
    # Evita di attribuire quote a un'altra partita con nomi simili.
    if len(candidates) > 1 and candidates[0][:2] == candidates[1][:2] and candidates[0][2] != candidates[1][2]:
        return None
    return candidates[0][2]


def current_quarter(match):
    """Rileva Q1-Q4 solo da indicazioni esplicite del periodo corrente."""
    live = match.get("live")
    period = live.get("period") if isinstance(live, dict) else None
    status = match.get("status")
    for value in (period, status):
        if value is None:
            continue
        label = str(value).strip().lower()
        if label in ("1", "2", "3", "4"):
            return int(label)
        found = re.fullmatch(
            r"(?:q\s*([1-4])|([1-4])(?:st|nd|rd|th)?\s*(?:quarter|qtr))",
            label,
        )
        if found:
            return int(found.group(1) or found.group(2))
    return None


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
    skipped_period = 0
    skipped_no_favorite = 0
    skipped_incomplete = 0
    missing_favorite_examples = []

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
            skipped_incomplete += 1
            continue
        game_code = match.get("code")
        if not game_code:
            continue
        game_id = f"sportstats:{game_code}"
        quarter = current_quarter(match)
        if quarter is None:
            skipped_period += 1
            continue
        alert_id = f"{game_id}:Q{quarter}"
        if alert_id in state["alerts"]:
            continue

        favorite = state["favorites"].get(game_id)
        if favorite not in ("home", "away"):
            favorite = find_prematch_favorite(match, state)
            if favorite:
                state["favorites"][game_id] = favorite
                save_state(state)
        if not favorite:
            skipped_no_favorite += 1
            if len(missing_favorite_examples) < 8:
                missing_favorite_examples.append({
                    "home": home, "away": away,
                    "start": match.get("start_at"),
                    "league": match.get("league_name"),
                })
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
            f"⏱ Periodo: {quarter}° quarto\n\n"
            f"⭐ Favorita pre-match: {favorite_name}\n"
            f"📉 Svantaggio: {abs(home_score - away_score)} punti\n\n"
            "Dati: Stadiro / SportStatsAPI (sportstatsapi.com)"
        )
        send_telegram(message)
        state["alerts"].append(alert_id)
        save_state(state)
        sent += 1
        print("ALERT INVIATO:", alert_id)

    save_state(state)
    print("Live con favorita nota:", with_favorite)
    print("Scartate senza periodo riconosciuto:", skipped_period)
    print("Scartate senza favorita associata:", skipped_no_favorite)
    print("Esempi partite senza favorita:", json.dumps(missing_favorite_examples, ensure_ascii=False))
    print("Scartate con dati incompleti:", skipped_incomplete)
    print("Live ignorate per dati stale:", skipped_stale)
    print("Nuovi alert:", sent)
    print("Quote pre-match in cache:", len(state["odds_games"]))
    print("Campionati quote configurati:", ", ".join(ODDS_SPORTS))


if __name__ == "__main__":
    main()

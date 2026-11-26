import json
import math
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import api_monitor
import basket_telegram_outcomes
api_monitor.install()

SPORTSTATS_API_KEY = os.environ["SPORTSTATS_API_KEY"]
ODDS_API_KEY = os.environ["ODDS_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = Path("basket_state.json")

# Monitoraggio H24 ogni 20 minuti; budget prudenziale 90 richieste UTC.
# Quote: 5 campionati ogni 24h e EuroLeague ogni 12h (circa 180 crediti/mese, se disponibili).
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
    "fcbayernmunchen": "bayernmunich",
    "bayernmunchen": "bayernmunich",
    "crvenazvezdameridianbet": "crvenazvezda",
    "crvenazvezda": "crvenazvezda",
    "redstarbelgrade": "crvenazvezda",
    "virtusbologna": "virtusbologna",
    "virtussegafredobologna": "virtusbologna",
    "fcbayernmunich": "bayernmunich",
    "bayernmuenchen": "bayernmunich",
    "bayernmunich": "bayernmunich",
    "baskonia": "baskonia",
    "saskibaskonia": "baskonia",
    "baskoniavitoriagasteiz": "baskonia",
    "besiktas": "besiktas",
    "besiktasjk": "besiktas",
    "besiktasistanbul": "besiktas",
}


def team_key(name):
    """Normalizza solo varianti editoriali sicure, senza indovinare squadre."""
    value = unicodedata.normalize("NFKD", name or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.casefold().replace("&", " and ")
    # Abbreviazioni editoriali frequenti, senza rimuovere citta' o nomi.
    value = re.sub(r"\b(?:basketball|basket|bc|bk|b c)\b", " ", value)
    key = re.sub(r"[^a-z0-9]", "", value)
    return TEAM_ALIASES.get(key, key)


def load_state():
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    else:
        state = {}
    state.setdefault("favorites", {})
    state.setdefault("favorite_prices", {})
    state.setdefault("alerts", [])
    state.setdefault("attempted", {})
    state.setdefault("odds_games", {})
    state.setdefault("odds_refreshed", {})
    state.setdefault("telegram_alerts", {})
    return state


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


EXTRA_ODDS_KEYS = ("basketball_eurocup", "basketball_spain_acb",
                   "basketball_italy_lega_basket", "basketball_germany_bbl",
                   "basketball_france_lnb", "basketball_wnba")
ODDS_RESERVE = 100


def discover_odds(state, now):
    last = parse_time(state.get("odds_catalog_at"))
    if last and now - last < timedelta(hours=24):
        return
    state["odds_catalog_at"] = now.isoformat()
    try:
        response = requests.get("https://api.the-odds-api.com/v4/sports/",
                                params={"apiKey": ODDS_API_KEY}, timeout=25)
        response.raise_for_status()
        games = response.json()
        if not isinstance(games, list):
            raise ValueError("Invalid catalog")
        active = {item.get("key") for item in games
                  if isinstance(item, dict) and item.get("active") is True}
        state["odds_extra"] = [key for key in EXTRA_ODDS_KEYS if key in active][:3]
        state["odds_remaining"] = int(response.headers["x-requests-remaining"])
        print("Campionati quote aggiuntivi:", state["odds_extra"])
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        state["odds_remaining"] = None
        print("Catalogo quote non disponibile:", type(exc).__name__)
    save_state(state)


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
    return response.json(), response.headers.get("x-requests-remaining")


def update_odds(state, now):
    discover_odds(state, now)
    for sport in (*ODDS_SPORTS, *state.get("odds_extra", [])):
        remaining = state.get("odds_remaining")
        if not isinstance(remaining, int) or remaining <= ODDS_RESERVE:
            print("Quote sospese: riserva crediti o saldo non disponibile")
            break
        last = parse_time(state["odds_refreshed"].get(sport))
        refresh_hours = EUROLEAGUE_REFRESH_HOURS if sport == "basketball_euroleague" else ODDS_REFRESH_HOURS
        if last and now - last < timedelta(hours=refresh_hours):
            continue
        try:
            games, remaining = fetch_odds(sport)
            state["odds_remaining"] = int(remaining) if remaining is not None else None
            save_state(state)
        except (requests.RequestException, ValueError) as exc:
            print("ERRORE QUOTE:", sport, type(exc).__name__)
            state["odds_remaining"] = None
            save_state(state)
            break

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
            selected_prices = None
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
                    selected_prices = {"home": prices[home], "away": prices[away]}
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
                "prices": selected_prices,
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


def update_hoops(state, now):
    key = os.environ.get("HOOPS_API_KEY")
    if not key:
        return
    last = parse_time(state.get("hoops_attempt_at"))
    if last and now - last < timedelta(hours=12):
        return
    state["hoops_attempt_at"] = now.isoformat()
    save_state(state)
    try:
        response = requests.get("https://hoopsapi.com/api/v1/games",
                                params={"competition": "Italy - Serie A2"},
                                headers={"Authorization": "Bearer " + key}, timeout=25)
        response.raise_for_status()
        games = response.json()["games"]
        if not isinstance(games, list):
            raise ValueError("Invalid games")
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        print("HoopsAPI error:", type(exc).__name__)
        return
    count = 0
    for game in games:
        if not isinstance(game, dict) or game.get("competition") != "Italy - Serie A2":
            continue
        start = parse_time(game.get("start_time"))
        home, away = game.get("home"), game.get("away")
        if not start or start <= now + timedelta(minutes=5) or not isinstance(home, str) or not isinstance(away, str) or not home or not away:
            continue
        providers = game.get("providers") or {}
        for book in ("pinnacle", "1xbet", "cloudbet"):
            entry = providers.get(book) or {}
            captured = parse_time(entry.get("captured_at"))
            if not captured or captured >= start or captured > now:
                continue
            market = entry.get("moneyline") or {}
            try:
                h, a = float(market["home"]), float(market["away"])
            except (KeyError, ValueError, TypeError):
                continue
            if not all(math.isfinite(x) and x > 1 for x in (h, a)) or h == a:
                continue
            state["odds_games"]["hoops:" + str(game.get("game_id") or f"{start}:{home}:{away}")] = {
                "sport": "hoops_italy_a2", "home": home, "away": away,
                "start": start.isoformat(), "favorite": "home" if h < a else "away",
                "prices": {"home": h, "away": a}, "captured_at": now.isoformat(),
                "source": "hoopsapi", "bookmaker": book,
            }
            count += 1
            break
    save_state(state)
    print("HoopsAPI Serie A2 pre-match:", count)


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
    league = (match.get("league_name") or "").casefold()
    league_sports = {
        "serie a2": "hoops_italy_a2",
        "lega a2": "hoops_italy_a2",
        "euroleague": "basketball_euroleague",
        "euro league": "basketball_euroleague",
        "nba": "basketball_nba",
        "australia: nbl": "basketball_nbl",
        "australian nbl": "basketball_nbl",
        "wncaa": "basketball_wncaab",
        "ncaa women": "basketball_wncaab",
        "ncaa": "basketball_ncaab",
        "ncaab": "basketball_ncaab",
    }
    expected_sport = next((sport for label, sport in league_sports.items() if label in league), None)
    candidates = []
    for item in state["odds_games"].values():
        if item.get("sport") == "hoops_italy_a2" and expected_sport != "hoops_italy_a2":
            continue
        if expected_sport and item.get("sport") != expected_sport:
            continue
        captured = parse_time(item.get("captured_at"))
        if captured and captured >= start:
            continue
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
            candidates.append((0 if exact else 1, difference, item["favorite"], item.get("prices"), False))
        elif reversed_exact or reversed_loose:
            inverted = "away" if item["favorite"] == "home" else "home"
            candidates.append((0 if reversed_exact else 1, difference, inverted, item.get("prices"), True))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: (pair[0], pair[1]))
    # Evita di attribuire quote a un'altra partita con nomi simili.
    if len(candidates) > 1 and candidates[0][:2] == candidates[1][:2] and candidates[0][2] != candidates[1][2]:
        return None
    best = candidates[0]
    prices = best[3]
    if isinstance(prices, dict) and best[4]:
        prices = {"home": prices.get("away"), "away": prices.get("home")}
    return {"favorite": best[2], "prices": prices}



def diagnose_missing_odds(match, state):
    """Diagnostica prudente: non associare mai quote di incontri estranei."""
    league = (match.get("league_name") or "").casefold()
    known_leagues = {
        "hoops_italy_a2": ("serie a2", "lega a2"),
        "basketball_wnba": ("wnba",),
        "basketball_eurocup": ("eurocup", "euro cup"),
        "basketball_spain_acb": ("acb", "liga endesa"),
        "basketball_italy_lega_basket": ("lega basket", "italy: serie a"),
        "basketball_germany_bbl": ("germany: bbl", "basketball bundesliga"),
        "basketball_france_lnb": ("lnb", "betclic elite"),
        "basketball_nba": ("nba",),
        "basketball_euroleague": ("euroleague", "euro league"),
        "basketball_nbl": ("australia: nbl", "australian nbl"),
        "basketball_ncaab": ("ncaa", "ncaab"),
        "basketball_wncaab": ("wncaa", "ncaa women"),
    }
    supported = [sport for sport, labels in known_leagues.items()
                 if any(label in league for label in labels)]
    supported = [sport for sport in supported if sport in (*ODDS_SPORTS, *state.get("odds_extra", []), "hoops_italy_a2")]
    if "basketball_wnba" in supported:
        supported = ["basketball_wnba"]
    if "basketball_wncaab" in supported:
        supported = ["basketball_wncaab"]
    if not supported:
        return {"reason": "campionato_non_coperto_dalle_quote_configurate",
                "league": match.get("league_name")}

    start = parse_time(match.get("start_at"))
    if not start:
        return {"reason": "orario_live_mancante"}
    home = team_key((match.get("home_team") or {}).get("name"))
    away = team_key((match.get("away_team") or {}).get("name"))
    same_league = [item for item in state["odds_games"].values()
                   if item.get("sport") in supported]
    if not same_league:
        return {"reason": "nessuna_quota_memorizzata_per_campionato",
                "sport": supported}
    nearby = []
    for item in same_league:
        odds_start = parse_time(item.get("start"))
        if not odds_start:
            continue
        minutes = round(abs((start - odds_start).total_seconds()) / 60)
        if minutes > MAX_START_DIFFERENCE_HOURS * 60:
            continue
        odds_home, odds_away = team_key(item.get("home")), team_key(item.get("away"))
        overlap = int(home in (odds_home, odds_away)) + int(away in (odds_home, odds_away))
        nearby.append((overlap, minutes, item))
    if not nearby:
        return {"reason": "nessuna_quota_in_finestra_oraria", "sport": supported}
    nearby.sort(key=lambda x: (-x[0], x[1]))
    overlap, minutes, item = nearby[0]
    result = {"reason": ("nomi_squadre_non_corrispondenti" if overlap == 0 else
                         "solo_una_squadra_corrisponde" if overlap == 1 else
                         "possibile_disallineamento_orario_o_ambiguita"),
              "sport": item.get("sport"), "overlap_squadre": overlap}
    # Mostra dettagli soltanto quando almeno una squadra coincide.
    if overlap:
        result.update({"differenza_minuti": minutes,
                       "squadre_quote": [item.get("home"), item.get("away")]})
    return result


def current_quarter(match):
    """Rileva Q1-Q4 solo da indicazioni esplicite del periodo corrente."""
    live = match.get("live")
    period = live.get("period") if isinstance(live, dict) else None
    status = match.get("status")
    for value in (period, live.get("quarter") if isinstance(live, dict) else None,
                  match.get("current_quarter"), match.get("current_period"),
                  match.get("quarter"), status, match.get("status_code")):
        if value is None:
            continue
        label = str(value).strip().lower()
        if label in ("1", "2", "3", "4"):
            return int(label)
        found = re.fullmatch(
            r"(?:q(?:uarter|tr)?[ _-]*([1-4])|([1-4])(?:st|nd|rd|th)?[ _-]*(?:quarter|qtr|q))",
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
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram non ha confermato l'invio")
    return (payload.get("result") or {}).get("message_id")


def main():
    print("=== BASKET LIVE ALERT / SPORTSTATS + ODDS ===")
    state = load_state()
    now = utc_now()
    update_odds(state, now)
    update_hoops(state, now)
    save_state(state)

    budget = state.setdefault("sportstats_budget", {})
    day_key = now.date().isoformat()
    if budget.get("day") != day_key:
        budget.clear()
        budget.update(day=day_key, requests=0)
    def reserve():
        if budget.get("blocked") or budget["requests"] >= 90:
            return False
        budget["requests"] += 1
        save_state(state)
        return True
    def block_on_limit(exc):
        if getattr(exc, "response", None) is not None and exc.response.status_code == 429:
            budget["blocked"] = True
            save_state(state)
    if not reserve():
        print("SPORTSTATS budget esaurito o bloccato:", budget["requests"])
        return
    try:
        matches = fetch_live()
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        block_on_limit(exc)
        print("ERRORE SPORTSTATS:", str(exc))
        return
    print("Partite live:", len(matches))
    # Never infer a final from a game disappearing from the live feed.
    basket_telegram_outcomes.update(state, matches, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
    # Limit final-result lookups to recent alerts, avoiding perpetual API calls.
    recent_pending = [
        r for r in state["telegram_alerts"].values()
        if not r.get("finalized") and
        (parse_time(r.get("created_at")) is not None) and
        timedelta(0) <= now - parse_time(r["created_at"]) <= timedelta(days=2)
    ]
    local = now.astimezone(ZoneInfo("Europe/Rome"))
    slot = local.hour // 8
    slot_key = f"{local.date().isoformat()}:{slot}"
    if recent_pending and state.get("finished_slot") != slot_key and reserve():
        state["finished_slot"] = slot_key
        save_state(state)
        dates = sorted({parse_time(r["created_at"]).date().isoformat() for r in recent_pending})
        cursor = state.get("finished_cursor", 0)
        day = dates[(cursor // 3) % len(dates)]
        offset = (cursor % 3) * 100
        state["finished_cursor"] = cursor + 1
        save_state(state)
        try:
            response = requests.get(
                "https://api.sportstatsapi.com/api/v1/basketball/matches",
                headers={"Authorization": f"Bearer {SPORTSTATS_API_KEY}"},
                params={"date": day, "status": "FT", "limit": 100, "offset": offset},
                timeout=25,
            )
            print("SPORTSTATS FINISHED HTTP:", response.status_code)
            response.raise_for_status()
            payload = response.json()
            finished = payload.get("data", []) if payload.get("success") else []
            if isinstance(finished, list):
                basket_telegram_outcomes.update(state, finished, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
                if len(finished) < 100:
                    state["finished_cursor"] = (cursor // 3 + 1) * 3
        except (requests.RequestException, ValueError, TypeError) as exc:
            block_on_limit(exc)
            print("Risultati basket conclusi non disponibili:", type(exc).__name__)
    print("SPORTSTATS budget:", budget["requests"], "/ 90")
    save_state(state)
    sent = 0
    skipped_stale = 0
    with_favorite = 0
    skipped_period = 0
    missing_period_examples = []
    skipped_no_favorite = 0
    skipped_incomplete = 0
    missing_favorite_examples = []
    missing_favorite_reasons = {}

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
            if len(missing_period_examples) < 8:
                live_data = match.get("live")
                missing_period_examples.append({
                    "home": home, "away": away,
                    "league": match.get("league_name"),
                    "status": match.get("status"),
                    "status_code": match.get("status_code"),
                    "live_period": live_data.get("period") if isinstance(live_data, dict) else None,
                    "live_keys": sorted(live_data.keys()) if isinstance(live_data, dict) else [],
                })
            continue
        alert_id = f"{game_id}:Q{quarter}"
        if alert_id in state["alerts"]:
            continue

        favorite = state["favorites"].get(game_id)
        prices = state["favorite_prices"].get(game_id)
        if favorite not in ("home", "away") or not prices:
            matched = find_prematch_favorite(match, state)
            if matched:
                # Non cambiare una favorita gia' memorizzata.
                if favorite not in ("home", "away"):
                    favorite = matched["favorite"]
                    state["favorites"][game_id] = favorite
                if matched["favorite"] == favorite and matched.get("prices"):
                    prices = matched["prices"]
                    state["favorite_prices"][game_id] = prices
                save_state(state)
        if not favorite:
            skipped_no_favorite += 1
            diagnostic = diagnose_missing_odds(match, state)
            reason = diagnostic["reason"]
            missing_favorite_reasons[reason] = missing_favorite_reasons.get(reason, 0) + 1
            if len(missing_favorite_examples) < 8:
                missing_favorite_examples.append({
                    "home": home, "away": away,
                    "start": match.get("start_at"),
                    "league": match.get("league_name"),
                    "diagnostica": diagnostic,
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
        odds_text = ""
        if isinstance(prices, dict):
            try:
                home_odd = float(prices["home"])
                away_odd = float(prices["away"])
                odds_text = (f"📈 Quote pre-match: {home} {home_odd:.2f} | "
                             f"{away} {away_odd:.2f}\n")
            except (KeyError, TypeError, ValueError):
                pass
        message = (
            "🚨🏀 FAVORITA IN SVANTAGGIO!\n\n"
            f"🏆 Campionato: {match.get('league_name') or 'Non disponibile'}\n\n"
            f"🏠 {home}\n"
            f"✈️ {away}\n\n"
            f"📊 Risultato: {home_score} - {away_score}\n"
            f"⏱ Periodo: {quarter}° quarto\n\n"
            f"⭐ Favorita pre-match: {favorite_name}\n"
            f"{odds_text}"
            f"📉 Svantaggio: {abs(home_score - away_score)} punti\n\n"
            "Dati: Stadiro / SportStatsAPI (sportstatsapi.com)"
        )
        message_id = send_telegram(message)
        if message_id is not None:
            state["telegram_alerts"][alert_id] = {
                "game_code": str(game_code),
                "message_id": message_id,
                "text": message,
                "teams": [home, away],
                "favorite": favorite,
                "created_at": now.isoformat(),
                "finalized": False,
            }
        state["alerts"].append(alert_id)
        save_state(state)
        sent += 1
        print("ALERT INVIATO:", alert_id)

    save_state(state)
    print("Live con favorita nota:", with_favorite)
    print("Scartate senza periodo riconosciuto:", skipped_period)
    print("Esempi periodo non riconosciuto:", json.dumps(missing_period_examples, ensure_ascii=False))
    print("Scartate senza favorita associata:", skipped_no_favorite)
    print("Motivi mancata associazione favorita:", json.dumps(missing_favorite_reasons, ensure_ascii=False))
    print("Esempi partite senza favorita:", json.dumps(missing_favorite_examples, ensure_ascii=False))
    print("Scartate con dati incompleti:", skipped_incomplete)
    print("Live ignorate per dati stale:", skipped_stale)
    print("Nuovi alert:", sent)
    print("Quote pre-match in cache:", len(state["odds_games"]))
    cached_by_sport = {}
    for item in state["odds_games"].values():
        sport = item.get("sport", "sconosciuto")
        cached_by_sport[sport] = cached_by_sport.get(sport, 0) + 1
    print("Quote in cache per campionato:", json.dumps(cached_by_sport, ensure_ascii=False))
    print("Campionati quote configurati:", ", ".join(ODDS_SPORTS))


if __name__ == "__main__":
    main()

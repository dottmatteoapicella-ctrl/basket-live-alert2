import basket_state_crypto
import basket_moviodds
import basket_coverage
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
STATE_FILE = Path("basket_state.enc")

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
    "slbenficaw": "benficaw",
    "sportlisboaebenficaw": "benficaw",
    "zkkstudentnisw": "studentnisw",
    "panathinaikoswomen": "panathinaikosw",
    "panathinaikosfemale": "panathinaikosw",
}


def team_key(name):
    """Normalizza solo varianti editoriali sicure, senza indovinare squadre."""
    value = unicodedata.normalize("NFKD", name or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"\b(?:women|female|ladies)\b", "w", value)
    # Abbreviazioni editoriali frequenti, senza rimuovere citta' o nomi.
    value = re.sub(r"\b(?:basketball|basket|bc|bk|b c)\b", " ", value)
    key = re.sub(r"[^a-z0-9]", "", value)
    return TEAM_ALIASES.get(key, key)


def load_state():
    state = basket_state_crypto.load(STATE_FILE)
    state.setdefault("favorites", {})
    state.setdefault("favorite_prices", {})
    state.setdefault("alerts", [])
    state.setdefault("attempted", {})
    state.setdefault("odds_games", {})
    state.setdefault("odds_refreshed", {})
    state.setdefault("telegram_alerts", {})
    return state


def save_state(state):
    basket_state_crypto.save(STATE_FILE, state)


EXTRA_ODDS_KEYS = ("basketball_fiba_world_cup_qualification", "basketball_fiba_world_cup_qualifiers",
                   "basketball_new_zealand_tauihi", "basketball_new_zealand_tauihi_women",
                   "basketball_eurocup", "basketball_spain_acb",
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
        print("Catalogo basket attivo:", sorted(key for key in active if isinstance(key, str) and key.startswith("basketball_")))
        available = [key for key in EXTRA_ODDS_KEYS if key in active]
        cursor = state.get("odds_extra_cursor", 0) % max(1, len(available))
        state["odds_extra"] = (available[cursor:] + available[:cursor])[:3]
        state["odds_extra_cursor"] = cursor + len(state["odds_extra"])
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
                if home in prices and away in prices and all(math.isfinite(p) and p > 1 for p in (prices[home], prices[away])) and prices[home] != prices[away]:
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


HOOPS_LEAGUES = {
    "World - FIBA World Cup Qualification": ("hoops_fiba_qualifiers", ("world cup - qualification", "world cup qualification")),
    "New Zealand - Tauihi Women": ("hoops_tauihi_women", ("new zealand: tauihi women",)),
    "Lithuania - National Basketball League": ("hoops_lithuania_nkl", ("lithuania: nkl",)),
    "Bosnia and Herzegovina - Prvenstvo BIH": ("hoops_bosnia_bih", ("bosnia and herzegovina: prvenstvo bih",)),
    "Russia - Super League": ("hoops_russia_super", ("russia: super league",)),
    "Israel - National League": ("hoops_israel_national", ("israel: national league",)),
    "Australia - WNBL": ("hoops_australia_wnbl", ("australia: wnbl",)),
    "NBA": ("hoops_nba", ("usa: nba",)),
    "NCAA": ("hoops_ncaa", ("usa: ncaa",)),
    "Europe - Euroleague": ("hoops_euroleague", ("europe: euroleague", "euroleague")),
    "Italy - Serie A2": ("hoops_italy_a2", ("serie a2", "lega a2")),
    "Europe - Eurocup Women": ("hoops_eurocup_women", ("eurocup women", "eurocup femminile", "euro cup women")),
    "Italy - Serie A": ("hoops_italy_a1", ("italy: serie a", "lega a", "italy: lba")),
    "Spain - ACB": ("hoops_spain_acb", ("acb", "liga endesa")),
    "Germany - BBL": ("hoops_germany_bbl", ("germany: bbl", "basketball bundesliga")),
    "France - LNB": ("hoops_france_lnb", ("betclic elite", "france: lnb")),
    "Europe - Eurocup": ("hoops_eurocup", ("eurocup", "euro cup")),
}


def update_hoops(state, now):
    key = os.environ.get("HOOPS_API_KEY")
    if not key:
        return
    last = parse_time(state.get("hoops_attempt_at"))
    if last and now - last < timedelta(hours=24):
        return
    state["hoops_attempt_at"] = now.isoformat()
    save_state(state)
    headers = {"Authorization": "Bearer " + key}
    # The legacy bot used the unfiltered games endpoint once per refresh.
    try:
        response = requests.get("https://hoopsapi.com/api/v1/games", headers=headers, timeout=25)
        response.raise_for_status()
        games = response.json()["games"]
        if not isinstance(games, list):
            raise ValueError("Invalid global games")
        grouped = {}
        for game in games:
            if isinstance(game, dict) and isinstance(game.get("competition"), str):
                grouped.setdefault(game["competition"], []).append(game)
        print("HoopsAPI global games:", len(games), "competitions:", sorted(grouped))
        for competition, rows in grouped.items():
            if competition in HOOPS_LEAGUES:
                store_hoops_games(state, now, competition, HOOPS_LEAGUES[competition][0], rows)
        print("HoopsAPI unmapped global:", sorted(set(grouped) - set(HOOPS_LEAGUES)))
        if any(isinstance(g, dict) and g.get("source") == "hoopsapi" and g.get("captured_at") == now.isoformat() for g in state["odds_games"].values()):
            return
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        print("HoopsAPI global error:", type(exc).__name__)
        if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code in (401, 403, 429):
            return
    try:
        response = requests.get("https://hoopsapi.com/api/v1/competitions", headers=headers, timeout=25)
        response.raise_for_status()
        entries = response.json()["competitions"]
        names = {x if isinstance(x, str) else x.get("name", x.get("competition", "")) for x in entries if isinstance(x, (str, dict))}
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        print("HoopsAPI catalog error:", type(exc).__name__)
        return
    available = [name for name in HOOPS_LEAGUES if name in names]
    cursor = state.get("hoops_league_cursor", 0) % max(1, len(available))
    selected = (available[cursor:] + available[:cursor])[:6]
    state["hoops_league_cursor"] = cursor + len(selected)
    save_state(state)
    print("HoopsAPI supported catalog:", available)
    print("HoopsAPI unmapped catalog:", sorted(names - set(HOOPS_LEAGUES)))
    print("HoopsAPI selected leagues:", selected)
    for competition in selected:
        try:
            response = requests.get("https://hoopsapi.com/api/v1/games", params={"competition": competition}, headers=headers, timeout=25)
            response.raise_for_status()
            games = response.json()["games"]
            if not isinstance(games, list):
                raise ValueError("Invalid games")
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            print("HoopsAPI error:", type(exc).__name__)
            if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code in (401, 403, 429):
                break
            continue
        store_hoops_games(state, now, competition, HOOPS_LEAGUES[competition][0], games)


def store_hoops_games(state, now, competition, sport, games):
    count = 0
    for game in games:
        if not isinstance(game, dict) or game.get("competition") != competition:
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
                "sport": sport, "home": home, "away": away,
                "start": start.isoformat(), "favorite": "home" if h < a else "away",
                "prices": {"home": h, "away": a}, "captured_at": now.isoformat(),
                "source": "hoopsapi", "bookmaker": book,
            }
            count += 1
            break
    save_state(state)
    print("HoopsAPI pre-match:", competition, count)


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


def supported_odds_sports(league, state):
    league = league.casefold()
    labels = {
        "basketball_fiba_world_cup_qualification": ("world cup - qualification", "world cup qualification"),
        "basketball_fiba_world_cup_qualifiers": ("world cup - qualification", "world cup qualification"),
        "basketball_new_zealand_tauihi": ("new zealand: tauihi women",),
        "basketball_new_zealand_tauihi_women": ("new zealand: tauihi women",),
        "basketball_euroleague": ("euroleague", "euro league"),
        "basketball_nba": ("nba",),
        "basketball_nbl": ("australia: nbl", "australian nbl"),
        "basketball_ncaab": ("ncaa", "ncaab"),
        "basketball_wncaab": ("wncaa", "ncaa women", "ncaa - women"),
        "basketball_wnba": ("wnba",),
        "basketball_eurocup": ("eurocup", "euro cup"),
        "basketball_spain_acb": ("spain: acb", "liga endesa"),
        "basketball_italy_lega_basket": ("italy: serie a", "lega basket"),
        "basketball_germany_bbl": ("germany: bbl", "basketball bundesliga"),
        "basketball_france_lnb": ("france: lnb", "betclic elite"),
    }
    labels.update({sport: names for sport, names in HOOPS_LEAGUES.values()})
    active = set(ODDS_SPORTS) | set(state.get("odds_extra", [])) | {r.get("sport") for r in state.get("odds_games", {}).values() if isinstance(r, dict)}
    women = any(x in league for x in ("women", "female", "femmin", "wncaa", "wnba", "wnbl"))
    result = set()
    for sport, names in labels.items():
        if not (sport.startswith("hoops_") or sport in active):
            continue
        if not any(name in league for name in names):
            continue
        female_sport = sport in {"basketball_wnba", "basketball_wncaab", "hoops_eurocup_women", "hoops_australia_wnbl", "hoops_tauihi_women", "basketball_new_zealand_tauihi", "basketball_new_zealand_tauihi_women"}
        if women != female_sport:
            continue
        if sport in {"basketball_nba", "hoops_nba"} and any(x in league for x in ("g league", "summer")):
            continue
        if sport in {"basketball_italy_lega_basket", "hoops_italy_a1"} and "a2" in league:
            continue
        result.add(sport)
    return result


def valid_favorite_prices(favorite, prices):
    if favorite not in ("home", "away") or not isinstance(prices, dict):
        return False
    try:
        h, a = float(prices["home"]), float(prices["away"])
    except (KeyError, TypeError, ValueError):
        return False
    return all(math.isfinite(p) and p > 1 for p in (h, a)) and (h < a if favorite == "home" else a < h)


def find_prematch_favorite(match, state):
    home = normalize((match.get("home_team") or {}).get("name"))
    away = normalize((match.get("away_team") or {}).get("name"))
    start = parse_time(match.get("start_at"))
    if not home or not away or not start:
        return None
    league = (match.get("league_name") or "").casefold()
    supported = supported_odds_sports(league, state)
    candidates = []
    for item in state["odds_games"].values():
        item_sport = item.get("sport", "")
        if item_sport not in supported:
            continue
        captured = parse_time(item.get("captured_at"))
        if not captured or captured >= start:
            continue
        odds_start = parse_time(item.get("start"))
        if not odds_start or captured >= odds_start or abs((start - odds_start).total_seconds()) > MAX_START_DIFFERENCE_HOURS * 3600:
            continue
        prices = item.get("prices")
        if not valid_favorite_prices(item.get("favorite"), prices):
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
            candidates.append((0 if exact else 1, difference, item["favorite"], item.get("prices"), False, item.get("source","the_odds_api")))
        elif reversed_exact or reversed_loose:
            inverted = "away" if item["favorite"] == "home" else "home"
            candidates.append((0 if reversed_exact else 1, difference, inverted, item.get("prices"), True, item.get("source","the_odds_api")))
    if not candidates:
        return basket_moviodds.favorite(match,state,parse_time,normalize)
    candidates.sort(key=lambda pair: (pair[0], pair[1]))
    # Evita di attribuire quote a un'altra partita con nomi simili.
    if len(candidates) > 1 and candidates[0][:2] == candidates[1][:2] and candidates[0][2] != candidates[1][2]:
        return None
    best = candidates[0]
    prices = best[3]
    if isinstance(prices, dict) and best[4]:
        prices = {"home": prices.get("away"), "away": prices.get("home")}
    return {"favorite": best[2], "prices": prices, "source": best[5]}



def diagnose_missing_odds(match, state):
    """Diagnostica prudente: non associare mai quote di incontri estranei."""
    league = (match.get("league_name") or "").casefold()
    supported = sorted(supported_odds_sports(league, state))
    movi_league = basket_moviodds.LEAGUES.get(normalize(league), normalize(league))
    movi_count = sum(r.get("league") == movi_league for r in state.get("moviodds_games", {}).values() if isinstance(r, dict))
    if not supported and movi_count:
        return {"reason": "moviodds_quote_presenti_ma_matching_non_valido", "cached": movi_count}
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
    if not same_league and movi_count:
        return {"reason": "moviodds_quote_presenti_ma_matching_non_valido", "cached": movi_count}
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
    """Accept explicit, consistent quarter labels only."""
    live = match.get("live")
    live = live if isinstance(live, dict) else {}
    values = (live.get("period"), live.get("quarter"), match.get("current_quarter"),
              match.get("current_period"), match.get("quarter"))
    found = set()
    for value in values + (match.get("status"), match.get("status_code")):
        if isinstance(value, dict):
            value = value.get("name") or value.get("label") or value.get("number")
        if value is None or isinstance(value, bool):
            continue
        label = str(value).strip().lower()
        if label in ("halftime", "half time", "finished", "final", "ot", "overtime"):
            return None
        if label in ("1", "2", "3", "4"):
            found.add(int(label))
            continue
        m = re.fullmatch(r"(?:q(?:uarter|tr)?[ _-]*([1-4])|([1-4])(?:st|nd|rd|th)?[ _-]*(?:quarter|qtr|q)|period[ _-]*([1-4]))", label)
        if m:
            found.add(int(next(x for x in m.groups() if x)))
    return next(iter(found)) if len(found) == 1 else None


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
    movi_enabled = os.environ.get("MOVIODDS_BASKET_ENABLED")=="true"
    print("BASKET_MOVIODDS_STATUS:", json.dumps({"enabled":movi_enabled,"key_configured":bool(os.environ.get("MOVIODDS_API_KEY")),"cached":len(state.get("moviodds_games",{}))}))
    basket_moviodds.refresh(state,now,parse_time,normalize,os.environ.get("MOVIODDS_API_KEY",""),movi_enabled,save_state)
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
    basket_telegram_outcomes.settle_q3(state, matches)
    # Limit final-result lookups to recent alerts, avoiding perpetual API calls.
    recent_pending = [
        r for r in list(state["telegram_alerts"].values()) + list(state.get("shadow_q3", {}).values())
        if not r.get("finalized") and
        (parse_time(r.get("created_at")) is not None) and
        timedelta(0) <= now - parse_time(r["created_at"]) <= timedelta(days=7)
    ]
    local = now.astimezone(ZoneInfo("Europe/Rome"))
    slot = local.hour // 2
    slot_key = f"{local.date().isoformat()}:{slot}"
    # Reserve one live request for every remaining 20-minute UTC slot.
    remaining_live = 71 - (now.hour * 3 + now.minute // 20)
    if recent_pending and budget["requests"] + remaining_live < 90 and state.get("finished_slot") != slot_key and reserve():
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
                print("SPORTSTATS final recovery:", day, "offset:", offset, "rows:", len(finished))
                basket_telegram_outcomes.update(state, finished, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
                basket_telegram_outcomes.settle_q3(state, finished)
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
        gid="sportstats:"+str(match.get("code"))
        basket_coverage.observe(state,match,now,current_quarter(match) if match.get("status_code")=="live" else None,state["favorites"].get(gid),state.get("favorite_sources",{}).get(gid),check_shadow=False)
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
        if quarter == 4:
            shadow_favorite = state["favorites"].get(game_id)
            if shadow_favorite not in ("home", "away"):
                shadow_match = find_prematch_favorite(match, state)
                shadow_favorite = shadow_match["favorite"] if shadow_match else None
            basket_telegram_outcomes.observe_q3(state, match, quarter, shadow_favorite, now)
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
                    state.setdefault("favorite_sources",{})[game_id]=matched.get("source","unknown")
                save_state(state)
        basket_coverage.observe(state,match,now,quarter,favorite,state.get("favorite_sources",{}).get(game_id))
        if not valid_favorite_prices(favorite, prices):
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
        try:
            message_id = send_telegram(message)
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            print("BASKET_DELIVERY_UNCONFIRMED:", alert_id, type(exc).__name__)
            continue
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
            print("BASKET_DELIVERY_UNCONFIRMED:", alert_id, "missing_message_id")
            continue
        if message_id is not None:
            state["telegram_alerts"][alert_id] = {
                "game_code": str(game_code),
                "message_id": message_id,
                "text": message,
                "teams": [home, away],
                "favorite": favorite,
                "created_at": now.isoformat(),
                "finalized": False,
                "league": match.get("league_name"),
                "quarter": quarter,
                "deficit": abs(home_score - away_score),
                "provider": state.get("favorite_sources",{}).get(game_id,"unknown"),
                "favorite_odds": prices.get(favorite) if isinstance(prices, dict) else None,
            }
        state["alerts"].append(alert_id)
        save_state(state)
        sent += 1
        print("ALERT INVIATO:", alert_id)

    print("BASKET_COVERAGE_REPORT:",json.dumps(basket_coverage.report(state,now,parse_time),ensure_ascii=False))
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
    print("BASKET_SHADOW_Q3:", json.dumps(basket_telegram_outcomes.shadow_report(state)))
    print("BASKET_OUTCOME_REPORT:", json.dumps(basket_telegram_outcomes.report(state), ensure_ascii=False))
    print("Quote pre-match in cache:", len(state["odds_games"]))
    cached_by_sport = {}
    for item in state["odds_games"].values():
        sport = item.get("sport", "sconosciuto")
        cached_by_sport[sport] = cached_by_sport.get(sport, 0) + 1
    print("Quote in cache per campionato:", json.dumps(cached_by_sport, ensure_ascii=False))
    print("BASKET_MOVIODDS_CACHE:", len(state.get("moviodds_games",{})))
    print("Campionati quote configurati:", ", ".join(ODDS_SPORTS))


if __name__ == "__main__":
    main()


import os
import requests

api_key = os.environ["ODDS_API_KEY"]

response = requests.get(
    "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/",
    params={
        "apiKey": api_key,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal"
    },
    timeout=20
)

print("HTTP:", response.status_code)
print("Crediti rimasti:", response.headers.get("x-requests-remaining"))
print("Crediti utilizzati:", response.headers.get("x-requests-used"))

response.raise_for_status()

games = response.json()
print("Partite NBA con quote:", len(games))

if games:
    game = games[0]
    print("Casa:", game.get("home_team"))
    print("Trasferta:", game.get("away_team"))
    print("Inizio:", game.get("commence_time"))

    bookmakers = game.get("bookmakers", [])
    if bookmakers:
        print("Quote esempio:", bookmakers[0].get("markets"))

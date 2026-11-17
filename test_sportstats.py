
import os
import requests

key = os.environ["SPORTSTATS_API_KEY"]

response = requests.get(
    "https://api.sportstatsapi.com/api/v1/basketball/matches/live",
    headers={"Authorization": f"Bearer {key}"},
    timeout=20
)

print("HTTP:", response.status_code)
response.raise_for_status()

matches = response.json().get("data", [])
print("Partite LIVE:", len(matches))

for match in matches[:3]:
    print("PARTITA:", {
        "league": match.get("league_name"),
        "status": match.get("status"),
        "status_code": match.get("status_code"),
        "home": match.get("home_team"),
        "away": match.get("away_team"),
        "live": match.get("live"),
        "freshness": match.get("freshness")
    })

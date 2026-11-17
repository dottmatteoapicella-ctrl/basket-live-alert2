
import os
import requests

key = os.environ["SPORTSTATS_API_KEY"]

response = requests.get(
    "https://api.sportstatsapi.com/api/v1/basketball/matches",
    headers={"Authorization": f"Bearer {key}"},
    params={
        "date": "2026-11-18",
        "limit": 100
    },
    timeout=20
)

print("HTTP:", response.status_code)
response.raise_for_status()

data = response.json()
matches = data.get("data", [])

print("Partite totali:", len(matches))
print("Altre pagine:", data.get("meta", {}).get("has_more"))

nba = [
    match for match in matches
    if match.get("league_code") == "l1jy"
    or match.get("league_name", "").strip().upper() == "NBA"
]

print("Partite NBA:", len(nba))

for match in nba:
    print(
        match.get("home_team", {}).get("name"),
        "vs",
        match.get("away_team", {}).get("name"),
        "|",
        match.get("start_at")
    )


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

print("Totale LIVE:", len(matches))

for match in matches:
    league = match.get("league_name", "")
    if "NBA" in league.upper():
        print("NBA:", match)

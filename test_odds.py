
import os
import requests

api_key = os.environ["ODDS_API_KEY"]

url = "https://api.the-odds-api.com/v4/sports/"

response = requests.get(
    url,
    params={"apiKey": api_key},
    timeout=20
)

print("HTTP:", response.status_code)
response.raise_for_status()

sports = response.json()

basketball = [
    sport for sport in sports
    if "basketball" in sport.get("key", "").lower()
]

print("Campionati basket disponibili:", len(basketball))

for sport in basketball:
    print(
        sport.get("key"),
        "|",
        sport.get("title"),
        "| attivo:",
        sport.get("active")
    )

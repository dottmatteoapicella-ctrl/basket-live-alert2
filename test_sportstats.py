
import os
import requests

API_KEY = os.environ["SPORTSTATS_API_KEY"]

url = "https://api.sportstatsapi.com/api/v1/basketball/matches/live"

response = requests.get(
    url,
    headers={
        "Authorization": f"Bearer {API_KEY}"
    },
    timeout=20,
)

print("HTTP:", response.status_code)
response.raise_for_status()

data = response.json()

print("Chiavi risposta:", list(data.keys()))

matches = data.get("data", [])

print("Partite basket LIVE:", len(matches))

if matches:
    print("Esempio partita:")
    print(str(matches[0])[:2000])
else:
    print("Nessuna partita LIVE restituita.")

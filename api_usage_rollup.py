"""Aggregate per-run API requests into durable daily/monthly totals."""
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPORT = Path("api_monitor.json")
TOTALS = Path("api_usage_totals.json")


def main():
    if not REPORT.exists():
        print("API_USAGE: no run report; skipping")
        return
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    run_id = str(os.environ.get("GITHUB_RUN_ID", "manual"))
    data = json.loads(TOTALS.read_text(encoding="utf-8")) if TOTALS.exists() else {
        "schema_version": 1, "daily": {}, "monthly": {}, "processed_runs": []}
    if run_id in data["processed_runs"]:
        print("API_USAGE: run already counted", run_id)
        return
    for event in report.get("requests", []):
        timestamp = event.get("utc", "")
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        service = event.get("service", "unknown")
        for scope, key in (("daily", dt.strftime("%Y-%m-%d")), ("monthly", dt.strftime("%Y-%m"))):
            entry = data[scope].setdefault(key, {}).setdefault(service, {
                "requests": 0, "http_errors": 0, "rate_limits": 0,
                "reported_credits_charged": 0, "credit_charge_observations": 0,
                "last_reported_remaining": None})
            entry["requests"] += 1
            status = event.get("status")
            if status is None or status >= 400:
                entry["http_errors"] += 1
            if status == 429:
                entry["rate_limits"] += 1
            charge = event.get("credits_last_request")
            if isinstance(charge, int) and charge >= 0:
                entry["reported_credits_charged"] += charge
                entry["credit_charge_observations"] += 1
            remaining = event.get("credits_remaining")
            if isinstance(remaining, int):
                entry["last_reported_remaining"] = remaining
    data["processed_runs"].append(run_id)
    data["updated_utc"] = datetime.now(timezone.utc).isoformat()
    TOTALS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    month = today[:7]
    print("API_USAGE_DAILY:", json.dumps(data["daily"].get(today, {}), ensure_ascii=False))
    print("API_USAGE_MONTHLY:", json.dumps(data["monthly"].get(month, {}), ensure_ascii=False))


if __name__ == "__main__":
    main()

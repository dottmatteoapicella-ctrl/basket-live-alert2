"""Bounded-by-existing-budget result pagination, without guessing match identity."""
def next_page(state, dates):
    dates = sorted(set(dates), reverse=True)
    page = state.get("finished_page") or {}
    day = page.get("day")
    offset = page.get("offset", 0)
    if day not in dates or not isinstance(offset, int) or offset < 0:
        return dates[0], 0
    return day, offset

def advance(state, dates, day, offset, rows, pending):
    dates = sorted(set(dates), reverse=True)
    wanted = {str(r["game_code"]) for r in pending if r.get("game_code") is not None and not r.get("finalized")}
    received = {str(r["code"]) for r in rows if isinstance(r, dict) and r.get("code") is not None}
    print("BASKET_RECOVERY_CODES:", "wanted", len(wanted), "received", len(received), "overlap", len(wanted & received))
    if len(rows) >= 100:
        state["finished_page"] = {"day": day, "offset": offset + 100}
    else:
        state["finished_page"] = {"day": dates[(dates.index(day)+1) % len(dates)], "offset": 0}

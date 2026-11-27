import unittest
from datetime import datetime,timezone,timedelta
import basket_coverage as c
class TestCoverage(unittest.TestCase):
    def test_report(self):
        now=datetime.now(timezone.utc)
        state={}
        row=dict(code=1,status_code="live",league_name="League",home_team={"score":10},away_team={"score":12})
        c.observe(state,row,now,1,check_shadow=False)
        self.assertFalse(state["coverage"]["1"].get("hypothetical_quarters"))
        c.observe(state,row,now,1)
        c.observe(state,row,now,1,"home","moviodds")
        state["telegram_alerts"]={"a":dict(message_id=1,provider="moviodds",league="League",favorite_odds=1.5,finalized=True,marker="✅")}
        r=c.report(state,now,datetime.fromisoformat)
        self.assertEqual(r["coverage_30d"]["League"],dict(matches=1,valid=1,favorites=1,hypothetical=1))
        self.assertEqual(r["alerts_by_odds_band"]["1.40-1.59"]["favorite_lost"],1)
        self.assertEqual(r["favorites_by_provider_30d"]["moviodds"],1)
        self.assertFalse(c.report(state,now+timedelta(days=31),datetime.fromisoformat)["coverage_30d"])

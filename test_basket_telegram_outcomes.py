import unittest
from unittest.mock import Mock, patch
import basket_telegram_outcomes as outcomes

def game(home_score, away_score, status="finished"):
    return {"code":"g1","status_code":status,"home_team":{"name":"A","score":home_score},
            "away_team":{"name":"B","score":away_score},"freshness":{"stale":False}}

def state(favorite="home"):
    return {"telegram_alerts":{"g1:Q3":{"game_code":"g1","message_id":123,
            "text":"Original alert","teams":["A","B"],"favorite":favorite,"finalized":False}}}

class Tests(unittest.TestCase):
    def test_no_final_live(self):
        self.assertIsNone(outcomes.verified_final(game(60,70,"live")))
    def test_no_final_stale(self):
        m=game(60,70)
        m["freshness"]["stale"]=True
        self.assertIsNone(outcomes.verified_final(m))
    @patch("basket_telegram_outcomes.requests.post")
    def test_favorite_loses_green(self, post):
        post.return_value=Mock()
        post.return_value.json.return_value={"ok":True}
        s=state()
        self.assertEqual(outcomes.update(s,[game(80,90)],"token","chat"),1)
        self.assertIn("✅ FAVORITA SCONFITTA",post.call_args.kwargs["data"]["text"])
        self.assertIn("80-90",post.call_args.kwargs["data"]["text"])
        self.assertEqual(outcomes.update(s,[game(80,90)],"token","chat"),0)
    @patch("basket_telegram_outcomes.requests.post")
    def test_favorite_wins_red(self, post):
        post.return_value=Mock()
        post.return_value.json.return_value={"ok":True}
        self.assertEqual(outcomes.update(state(),[game(90,80)],"token","chat"),1)
        self.assertIn("❌ FAVORITA VINCENTE",post.call_args.kwargs["data"]["text"])
    @patch("basket_telegram_outcomes.requests.post")
    def test_mismatched_teams_not_edited(self, post):
        m=game(80,90)
        m["home_team"]["name"]="Other"
        self.assertEqual(outcomes.update(state(),[m],"token","chat"),0)
        post.assert_not_called()

if __name__=="__main__":
    unittest.main()


class ReportTest(__import__("unittest").TestCase):
    def test_verified_denominator(self):
        from basket_telegram_outcomes import report
        rows = {"a":{"finalized":True,"marker":"✅ FAVORITA SCONFITTA","quarter":2},
                "b":{"finalized":True,"marker":"❌ FAVORITA VINCENTE","quarter":2},
                "c":{"finalized":False}}
        result = report({"telegram_alerts":rows})
        self.assertEqual(result["total"]["success_pct"],50)
        self.assertEqual(result["total"]["pending"],1)
        self.assertEqual(result["by_quarter"]["2"]["verified"],2)


class ShadowQ3Test(__import__("unittest").TestCase):
    def test_explicit_periods_and_final(self):
        from datetime import datetime, timezone
        from basket_telegram_outcomes import observe_q3, settle_q3, shadow_report
        m = {"code":"1","status_code":"live","home_team":{"name":"A","score":70,"period1":20,"period2":20,"period3":20},"away_team":{"name":"B","score":72,"period1":22,"period2":22,"period3":22}}
        state = {}
        observe_q3(state,m,4,"home",datetime.now(timezone.utc))
        observe_q3(state,m,4,"home",datetime.now(timezone.utc))
        self.assertEqual(shadow_report(state)["alerts"],1)
        self.assertEqual(state["shadow_q3"]["1"]["q3_score"],[60,66])
        m["status_code"]="finished"
        settle_q3(state,[m])
        self.assertEqual(shadow_report(state)["success_pct"],100)
    def test_missing_periods(self):
        from basket_telegram_outcomes import q3_score
        self.assertIsNone(q3_score({"home_team":{"score":70},"away_team":{"score":72}}))


class RecoveryPaginationTests(unittest.TestCase):
    def test_recent_first_and_beyond_300(self):
        from basket_recovery import next_page, advance
        state = {}
        dates = ["2026-11-27", "2026-11-28"]
        self.assertEqual(next_page(state, dates), ("2026-11-28", 0))
        rows = [{"code": i} for i in range(100)]
        for offset in (0, 100, 200, 300):
            advance(state, dates, dates[1], offset, rows, [])
            self.assertEqual(next_page(state, dates), (dates[1], offset+100))
        advance(state, dates, dates[1], 400, [], [])
        self.assertEqual(next_page(state, dates), (dates[0], 0))

    def test_removed_date(self):
        from basket_recovery import next_page
        self.assertEqual(next_page({"finished_page": {"day": "old", "offset": 300}}, ["new"]), ("new", 0))

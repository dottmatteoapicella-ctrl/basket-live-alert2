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

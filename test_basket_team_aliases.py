import ast
import re
import unicodedata
import unittest
from pathlib import Path

tree = ast.parse(Path(__file__).with_name("basket_alert.py").read_text(encoding="utf-8"))
nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in ("team_key", "current_quarter", "supported_odds_sports")) or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("TEAM_ALIASES", "HOOPS_LEAGUES") for t in n.targets))]
scope = {"re": re, "unicodedata": unicodedata, "ODDS_SPORTS": ("basketball_nba", "basketball_ncaab", "basketball_wncaab")}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "<team_key>", "exec"), scope)
team_key = scope["team_key"]

class TeamAliasesTest(unittest.TestCase):
    def test_verified_pair(self):
        self.assertEqual(team_key("Besiktas"), team_key("Beşiktaş J.K."))
        self.assertEqual(team_key("Paris"), team_key("Paris Basketball"))

    def test_euroleague_variants(self):
        self.assertEqual(team_key("Bayern"), team_key("FC Bayern München"))
        self.assertEqual(team_key("Crvena Zvezda Meridianbet"), team_key("Red Star Belgrade"))
        self.assertEqual(team_key("Virtus Bologna"), team_key("Virtus Segafredo Bologna"))

    def test_womens_labels(self):
        self.assertEqual(team_key("Panathinaikos W"), team_key("Panathinaikos Women"))
        self.assertNotEqual(team_key("Panathinaikos W"), team_key("Panathinaikos"))

    def test_distinct_teams(self):
        self.assertNotEqual(team_key("Besiktas"), team_key("Besiktas Women"))
        self.assertNotEqual(team_key("Paris"), team_key("Paris 2"))

class CoverageTest(unittest.TestCase):
    def test_extra_and_gender(self):
        supported = scope["supported_odds_sports"]
        state = {"odds_extra": ["basketball_eurocup", "basketball_italy_lega_basket"]}
        self.assertIn("basketball_eurocup", supported("Europe: Eurocup", state))
        self.assertEqual(supported("Europe: Eurocup Women", state), {"hoops_eurocup_women"})
        self.assertNotIn("basketball_italy_lega_basket", supported("Italy: Serie A2", state))
        self.assertNotIn("basketball_nba", supported("USA: NBA G League", state))
        self.assertEqual(team_key("SL Benfica Women"), team_key("Benfica W"))
        self.assertEqual(team_key("ZKK Student Niš Women"), team_key("Student Nis W"))
        self.assertNotEqual(team_key("Benfica"), team_key("Benfica W"))

class QuarterTest(unittest.TestCase):
    def test_explicit_quarters(self):
        q = scope["current_quarter"]
        self.assertEqual(q({"live": {"quarter": 3}}), 3)
        self.assertEqual(q({"current_period": "Quarter 2"}), 2)
        self.assertEqual(q({"status_code": "Q4"}), 4)
        self.assertIsNone(q({"status": "Live"}))
        self.assertIsNone(q({"status": "Halftime"}))
        self.assertIsNone(q({"status": "OT"}))

if __name__ == "__main__":
    unittest.main()

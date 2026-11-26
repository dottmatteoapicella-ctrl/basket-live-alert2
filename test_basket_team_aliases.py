import ast
import re
import unicodedata
import unittest
from pathlib import Path

tree = ast.parse(Path(__file__).with_name("basket_alert.py").read_text(encoding="utf-8"))
nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in ("team_key", "current_quarter")) or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TEAM_ALIASES" for t in n.targets))]
scope = {"re": re, "unicodedata": unicodedata}
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

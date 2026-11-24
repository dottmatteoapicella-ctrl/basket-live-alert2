import ast
import re
import unicodedata
import unittest
from pathlib import Path

tree = ast.parse(Path(__file__).with_name("basket_alert.py").read_text(encoding="utf-8"))
nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "team_key") or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TEAM_ALIASES" for t in n.targets))]
scope = {"re": re, "unicodedata": unicodedata}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "<team_key>", "exec"), scope)
team_key = scope["team_key"]

class TeamAliasesTest(unittest.TestCase):
    def test_verified_pair(self):
        self.assertEqual(team_key("Besiktas"), team_key("Beşiktaş J.K."))
        self.assertEqual(team_key("Paris"), team_key("Paris Basketball"))

    def test_distinct_teams(self):
        self.assertNotEqual(team_key("Besiktas"), team_key("Besiktas Women"))
        self.assertNotEqual(team_key("Paris"), team_key("Paris 2"))

if __name__ == "__main__":
    unittest.main()

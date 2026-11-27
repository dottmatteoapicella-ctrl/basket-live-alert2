import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import basket_moviodds as m
def parse(x): return datetime.fromisoformat(x) if x else None
def key(x): return ''.join(c for c in (x or '').lower() if c.isalnum())
class TestMovi(unittest.TestCase):
    def test_collection_and_matching(self):
        now=datetime(2026,11,27,12,tzinfo=timezone.utc)
        start=(now+timedelta(hours=1)).isoformat()
        row=dict(id=1,league="Greece Elite League",home="AS Anagennisi Petralonon",away="Kronos Ag.Dimitriou",in_play=False,kickoff_utc=start,polled_at=now.isoformat())
        row["quotes"]=[dict(group_name="Game",line="To Win",side=n,odds_dec=p,ts=now.isoformat()) for n,p in [(row["home"],1.5),(row["away"],2.5)]]
        records=m.collect({"matches":[row]},now,parse,key)
        self.assertEqual(len(records),1)
        match=dict(league_name="Greece: Elite League",start_at=start,home_team={"name":"Kronos Agiou Dimitriou"},away_team={"name":"Anagennisi"})
        state={"moviodds_games":records}
        self.assertEqual(m.favorite(match,state,parse,key)["favorite"],"away")
        state["moviodds_games"]["2"]=records["1"]
        self.assertIsNone(m.favorite(match,state,parse,key))
        for change in ({"in_play":True},{"league":"Ebasketball"},{"polled_at":(now-timedelta(hours=1)).isoformat()}):
            self.assertFalse(m.collect({"matches":[dict(row,**change)]},now,parse,key))
    @patch("basket_moviodds.requests.get")
    def test_disabled_and_spacing(self,get):
        now=datetime(2026,11,28,0,10,tzinfo=timezone.utc)
        state={"moviodds_last":(now-timedelta(hours=1)).isoformat()}
        m.refresh(state,now,parse,key,"key",False,lambda s:None)
        m.refresh(state,now,parse,key,"key",True,lambda s:None)
        get.assert_not_called()

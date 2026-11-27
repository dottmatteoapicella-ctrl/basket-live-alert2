import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import Fernet, InvalidToken
import basket_state_crypto as crypto

class StateCryptoTest(unittest.TestCase):
    def test_migration(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "basket_state.enc"
            state = {"alerts": ["example"], "budget": 42}
            p.with_suffix(".json").write_text(json.dumps(state))
            with patch.dict("os.environ", {"BASKET_STATE_KEY": Fernet.generate_key().decode()}):
                self.assertEqual(crypto.load(p), state)
                crypto.save(p, state)
                self.assertNotIn(b"example", p.read_bytes())
                self.assertEqual(crypto.load(p), state)
            with patch.dict("os.environ", {"BASKET_STATE_KEY": Fernet.generate_key().decode()}):
                with self.assertRaises(InvalidToken):
                    crypto.load(p)
            with patch.dict("os.environ", {"BASKET_STATE_KEY": ""}):
                with self.assertRaises(RuntimeError):
                    crypto.load(p)

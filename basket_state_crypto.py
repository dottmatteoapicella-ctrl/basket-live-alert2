"""Authenticated encryption for Basket state; never silently reset state."""
import json
import os
from pathlib import Path
from cryptography.fernet import Fernet

def cipher():
    key = os.environ.get("BASKET_STATE_KEY")
    if not key:
        raise RuntimeError("BASKET_STATE_KEY missing")
    return Fernet(key.encode("ascii"))

def load(path):
    path = Path(path)
    f = cipher()
    legacy = path.with_suffix(".json")
    if path.exists():
        data = json.loads(f.decrypt(path.read_bytes()))
    elif legacy.exists():
        data = json.loads(legacy.read_text(encoding="utf-8"))
    else:
        raise RuntimeError("Basket archive missing; refusing to reset")
    if not isinstance(data, dict):
        raise ValueError("Invalid Basket archive")
    return data

def save(path, state):
    path = Path(path)
    payload = cipher().encrypt(json.dumps(state, ensure_ascii=False).encode("utf-8"))
    temp = path.with_suffix(".tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)

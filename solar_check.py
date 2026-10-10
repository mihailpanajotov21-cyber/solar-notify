#!/usr/bin/env python3
"""Проверява Solarman облака и праща известие през ntfy, когато може да се зарежда колата.

Нищо лично не е в кода - всичко идва от GitHub Secrets (променливи на средата).
В логовете НЕ се печатат стойности (хранилището е публично).
"""
import hashlib
import json
import os
import sys
import time

import requests

# ---------- ПРАГОВЕ (могат да се сменят от Variables в GitHub) ----------
MAX_CONSUMPTION_W = float(os.environ.get("MAX_CONSUMPTION_W", "500"))
MIN_BATTERY_PCT = float(os.environ.get("MIN_BATTERY_PCT", "60"))
MIN_SOLAR_W = float(os.environ.get("MIN_SOLAR_W", "1000"))   # да не вдига тревога нощем
CONFIRM_CHECKS = int(os.environ.get("CONFIRM_CHECKS", "2"))  # поредни проверки
COOLDOWN_MIN = int(os.environ.get("COOLDOWN_MIN", "180"))    # пауза между известията
# -----------------------------------------------------------------------

BASE = os.environ.get("SOLARMAN_BASE_URL", "https://globalapi.solarmanpv.com")
APP_ID = os.environ["SOLARMAN_APP_ID"]
APP_SECRET = os.environ["SOLARMAN_APP_SECRET"]
EMAIL = os.environ["SOLARMAN_EMAIL"]
PASSWORD = os.environ["SOLARMAN_PASSWORD"]
DEVICE_SN = os.environ["INVERTER_SN"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
DISCOVER = os.environ.get("DISCOVER", "false").lower() == "true"
STATE_FILE = "state.json"

# Как се казват нужните стойности в данните на инвертора (по име, без значение от регистъра)
NAMES = {
    "soc": ["soc", "battery soc", "battery capacity"],
    "load": ["total consumption power", "consumption power", "total load power", "load power"],
    "solar": ["total dc input power", "total solar power", "total pv power", "pv power"],
}


def ntfy(text, title, priority="default", tags=""):
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=text.encode("utf-8"),
        headers={"Title": title.encode("utf-8"), "Priority": priority, "Tags": tags},
        timeout=20,
    )


def get_token():
    pw_hash = hashlib.sha256(PASSWORD.encode()).hexdigest().lower()
    r = requests.post(
        f"{BASE}/account/v1.0/token",
        params={"appId": APP_ID, "language": "en"},
        json={"appSecret": APP_SECRET, "email": EMAIL, "password": pw_hash},
        timeout=30,
    )
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError("Solarman не върна токен (провери Secrets)")
    return token


def get_data(token):
    r = requests.post(
        f"{BASE}/device/v1.0/currentData",
        params={"language": "en"},
        headers={"Authorization": f"bearer {token}"},
        json={"deviceSn": DEVICE_SN},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("dataList", [])


def to_watts_or_number(item):
    value = float(item["value"])
    if str(item.get("unit", "")).lower() == "kw":
        value *= 1000
    return value


def pick(data, kind):
    for wanted in NAMES[kind]:
        for item in data:
            if item.get("name", "").strip().lower() == wanted or item.get("key", "").lower() == wanted:
                try:
                    return to_watts_or_number(item)
                except (TypeError, ValueError):
                    continue
    return None


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"streak": 0, "last_sent": 0}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def main():
    data = get_data(get_token())

    if DISCOVER:
        lines = [f"{i.get('key')} | {i.get('name')} | {i.get('value')} {i.get('unit') or ''}" for i in data]
        ntfy("\n".join(lines)[:3800], "Solarman: всички стойности", tags="mag")
        print("Discover: списъкът е изпратен в ntfy.")
        return

    soc, load, solar = pick(data, "soc"), pick(data, "load"), pick(data, "solar")
    if soc is None or load is None or solar is None:
        ntfy("Не намирам батерия/потребление/панели в данните. Пусни 'Run workflow' с discover=true.",
             "Solar-notify: проблем", priority="high", tags="warning")
        sys.exit("Липсват стойности")

    state = load_state()
    ok = load < MAX_CONSUMPTION_W and soc > MIN_BATTERY_PCT and solar >= MIN_SOLAR_W
    state["streak"] = state.get("streak", 0) + 1 if ok else 0

    cooled = (time.time() - state.get("last_sent", 0)) > COOLDOWN_MIN * 60
    notified = False
    if state["streak"] >= CONFIRM_CHECKS and cooled:
        ntfy(
            f"Батерия {soc:.0f}%, потребление {load:.0f} W, панели {solar:.0f} W.\n"
            "Отвори ELNexus и потвърди старта на зареждането.",
            "Може да зареждаш колата", priority="high", tags="battery,sunny",
        )
        state["last_sent"] = time.time()
        state["streak"] = 0
        notified = True

    save_state(state)
    print("Проверка готова. Известие:", "да" if notified else "не")


if __name__ == "__main__":
    main()
  

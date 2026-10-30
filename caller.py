"""Create the Vapi screening agent and call shortlisted owners.

Usage:
    python caller.py setup              # create/update the Vapi assistant
    python caller.py run --dry-run      # show who would be called, place no calls
    python caller.py run                # call pending owners in shortlist.csv
    python caller.py run --limit 3      # call at most 3 owners
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

from prompts import STRUCTURED_DATA_SCHEMA, build_system_prompt, first_message

HERE = Path(__file__).parent
STATE = HERE / "state.json"
SHORTLIST = HERE / "shortlist.csv"
RESULTS = HERE / "results.csv"
API = "https://api.vapi.ai"
IST = ZoneInfo("Asia/Kolkata")

RESULT_FIELDS = [
    "called_at", "title", "locality", "owner_name", "owner_phone", "listing_url",
    "call_id", "ended_reason", "reached_owner", "available", "available_from",
    "lease_11_month_ok", "notice_1_month_ok", "no_lock_in_after_3_months",
    "power_backup", "fibre_available", "rent_quoted", "viewing_booked",
    "viewing_slot", "address_or_landmark", "callback_requested", "callback_time",
    "asked_if_ai", "notes", "summary",
]


# ---------- helpers ----------

def load_config() -> dict:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    v = cfg["vapi"]
    v["api_key"] = os.environ.get("VAPI_API_KEY") or v.get("api_key")
    v["phone_number_id"] = os.environ.get("VAPI_PHONE_NUMBER_ID") or v.get("phone_number_id")
    return cfg


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict):
    STATE.write_text(json.dumps(state, indent=2))


def headers(cfg):
    if not cfg["vapi"]["api_key"]:
        sys.exit("Missing VAPI_API_KEY (set env var or config.yaml).")
    return {"Authorization": f"Bearer {cfg['vapi']['api_key']}", "Content-Type": "application/json"}


def normalise_phone(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return "+" + digits


def within_calling_hours(cfg) -> bool:
    hour = datetime.now(IST).hour
    c = cfg["calling"]
    return c["allowed_start_hour"] <= hour < c["allowed_end_hour"]


# ---------- setup ----------

def assistant_payload(cfg) -> dict:
    v = cfg["vapi"]
    return {
        "name": "Apartment screening assistant",
        "firstMessage": first_message(cfg),
        "model": {
            "provider": "openai",
            "model": v["model"],
            "temperature": 0.4,
            "messages": [{"role": "system", "content": build_system_prompt(cfg)}],
        },
        "voice": {"provider": v["voice_provider"], "voiceId": v["voice_id"]},
        "transcriber": {"provider": "deepgram", "model": "nova-2", "language": "en-IN"},
        "endCallFunctionEnabled": True,
        "maxDurationSeconds": cfg["calling"]["max_call_seconds"],
        "analysisPlan": {
            "summaryPlan": {"enabled": True},
            "structuredDataPlan": {"enabled": True, "schema": STRUCTURED_DATA_SCHEMA},
        },
    }


def cmd_setup(cfg):
    state = load_state()
    payload = assistant_payload(cfg)
    if state.get("assistant_id"):
        r = requests.patch(f"{API}/assistant/{state['assistant_id']}", headers=headers(cfg), json=payload)
        action = "Updated"
    else:
        r = requests.post(f"{API}/assistant", headers=headers(cfg), json=payload)
        action = "Created"
    r.raise_for_status()
    state["assistant_id"] = r.json()["id"]
    save_state(state)
    print(f"{action} assistant {state['assistant_id']}")


# ---------- calling ----------

def read_shortlist():
    if not SHORTLIST.exists():
        sys.exit("No shortlist.csv. Run: python shortlist.py listings.csv")
    with open(SHORTLIST, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_shortlist(rows):
    with open(SHORTLIST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def append_result(record: dict):
    new = not RESULTS.exists()
    with open(RESULTS, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(record)


def place_call(cfg, assistant_id, row, slots):
    body = {
        "assistantId": assistant_id,
        "phoneNumberId": cfg["vapi"]["phone_number_id"],
        "customer": {"number": normalise_phone(row["owner_phone"]), "name": row.get("owner_name") or None},
        "assistantOverrides": {
            "variableValues": {
                "listing_title": row.get("title") or "3BHK flat",
                "listing_locality": row.get("locality") or "",
                "owner_name": row.get("owner_name") or "unknown",
                "viewing_slots": "; ".join(slots),
            }
        },
    }
    r = requests.post(f"{API}/call", headers=headers(cfg), json=body)
    r.raise_for_status()
    return r.json()["id"]


def wait_for_call(cfg, call_id, timeout=900):
    """Poll until the call ends and analysis is ready."""
    start = time.time()
    while time.time() - start < timeout:
        r = requests.get(f"{API}/call/{call_id}", headers=headers(cfg))
        r.raise_for_status()
        call = r.json()
        if call.get("status") == "ended":
            analysis = call.get("analysis") or {}
            if analysis.get("structuredData") or time.time() - start > 120:
                return call
        time.sleep(10)
    return call


def cmd_run(cfg, dry_run=False, limit=None):
    state = load_state()
    assistant_id = state.get("assistant_id")
    if not assistant_id and not dry_run:
        sys.exit("No assistant yet. Run: python caller.py setup")
    if not cfg["vapi"]["phone_number_id"] and not dry_run:
        sys.exit("Missing VAPI_PHONE_NUMBER_ID (set env var or config.yaml).")

    slots = state.get("open_slots", cfg["viewing_slots"])
    rows = read_shortlist()
    pending = [r for r in rows if r.get("call_status", "pending") == "pending"]
    if limit:
        pending = pending[:limit]

    if not pending:
        print("Nothing pending in shortlist.csv.")
        return

    for i, row in enumerate(pending):
        label = f"{row.get('title')} ({row.get('owner_name') or 'owner'}, {normalise_phone(row['owner_phone'])})"

        if not slots:
            print("All viewing slots are booked. Add more to config.yaml and delete open_slots from state.json.")
            break
        if dry_run:
            print(f"[dry run] would call {label}")
            continue
        if not within_calling_hours(cfg):
            print("Outside calling hours (IST). Stopping; run again later.")
            break

        print(f"Calling {label} ...")
        try:
            call_id = place_call(cfg, assistant_id, row, slots)
            call = wait_for_call(cfg, call_id)
        except requests.HTTPError as e:
            print(f"  Call failed: {e.response.status_code} {e.response.text[:200]}")
            row["call_status"] = "error"
            write_shortlist(rows)
            continue

        data = (call.get("analysis") or {}).get("structuredData") or {}
        summary = (call.get("analysis") or {}).get("summary", "")
        record = {
            "called_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M"),
            "title": row.get("title"), "locality": row.get("locality"),
            "owner_name": row.get("owner_name"), "owner_phone": row.get("owner_phone"),
            "listing_url": row.get("listing_url"), "call_id": call_id,
            "ended_reason": call.get("endedReason"), "summary": summary, **data,
        }
        append_result(record)

        booked = data.get("viewing_slot") if data.get("viewing_booked") else None
        if booked:
            slots = [s for s in slots if s.strip().lower() != booked.strip().lower()]
            state["open_slots"] = slots
            save_state(state)
            row["call_status"] = "viewing_booked"
            print(f"  VIEWING BOOKED: {booked} | {data.get('address_or_landmark', '')}")
        elif data.get("callback_requested"):
            row["call_status"] = "callback"
            print(f"  Callback requested: {data.get('callback_time', 'time not given')}")
        elif not data.get("reached_owner"):
            row["call_status"] = "no_answer"
            print(f"  Didn't reach owner ({call.get('endedReason')})")
        else:
            row["call_status"] = "not_a_fit"
            print(f"  Not a fit: {data.get('notes', summary)[:120]}")
        write_shortlist(rows)

        if i < len(pending) - 1:
            time.sleep(cfg["calling"]["gap_seconds"])

    print(f"\nDone. Results in {RESULTS.name}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    run = sub.add_parser("run")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--limit", type=int)
    args = ap.parse_args()

    cfg = load_config()
    if args.cmd == "setup":
        cmd_setup(cfg)
    else:
        cmd_run(cfg, dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()

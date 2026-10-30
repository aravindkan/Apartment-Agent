"""Score NoBroker listings against your preferences and write shortlist.csv.

Usage:
    python shortlist.py listings.csv
"""
import csv
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent


def load_config() -> dict:
    return yaml.safe_load((HERE / "config.yaml").read_text())


def to_int(value, default=None):
    try:
        return int(str(value).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return default


def evaluate(row: dict, prefs: dict):
    """Return (passes_hard_filters, score, reasons)."""
    reasons = []

    bhk = to_int(row.get("bhk"))
    if bhk != prefs["bhk"]:
        return False, 0, [f"BHK is {row.get('bhk') or 'unknown'}"]

    floor = to_int(row.get("floor"))
    if floor is None or not (prefs["floor_min"] <= floor <= prefs["floor_max"]):
        return False, 0, [f"floor {row.get('floor') or 'unknown'} outside {prefs['floor_min']}-{prefs['floor_max']}"]

    locality = (row.get("locality") or "").lower()
    if not any(loc.lower() in locality for loc in prefs["localities"]):
        return False, 0, [f"locality '{row.get('locality')}' not in target areas"]

    rent = to_int(row.get("rent"))
    if rent is None or rent > prefs["max_rent"]:
        return False, 0, [f"rent {row.get('rent') or 'unknown'} above {prefs['max_rent']}"]

    if not (row.get("owner_phone") or "").strip():
        return False, 0, ["no owner phone (unlock contact on NoBroker first)"]

    # Soft scoring: 100 base, adjusted by nice-to-haves and value
    score = 100
    backup = (row.get("power_backup") or "").strip().lower()
    if backup in ("yes", "full"):
        score += 15
        reasons.append("power backup")
    elif backup in ("no", "none"):
        score -= 15
        reasons.append("no power backup")

    # Cheaper relative to ceiling scores a little higher
    score += round(10 * (prefs["max_rent"] - rent) / prefs["max_rent"])

    # Mid-range floors (6-9) slightly preferred
    if 6 <= floor <= 9:
        score += 5

    return True, score, reasons or ["meets all hard filters"]


def main(path: str):
    cfg = load_config()
    prefs = cfg["preferences"]

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    passed, rejected = [], []
    for row in rows:
        ok, score, reasons = evaluate(row, prefs)
        row["score"] = score
        row["reason"] = "; ".join(reasons)
        (passed if ok else rejected).append(row)

    passed.sort(key=lambda r: r["score"], reverse=True)

    out = HERE / "shortlist.csv"
    fields = list(rows[0].keys()) if rows else []
    for extra in ("call_status",):
        if extra not in fields:
            fields.append(extra)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in passed:
            r.setdefault("call_status", "pending")
            w.writerow(r)

    print(f"{len(passed)} of {len(rows)} listings shortlisted -> {out.name}")
    for r in passed:
        print(f"  [{r['score']}] {r.get('title')} | floor {r.get('floor')} | ₹{r.get('rent')} | {r['reason']}")
    if rejected:
        print("\nRejected:")
        for r in rejected:
            print(f"  - {r.get('title')}: {r['reason']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python shortlist.py listings.csv")
    main(sys.argv[1])

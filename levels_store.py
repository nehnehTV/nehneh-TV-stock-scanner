"""
Shared store for manually entered price levels: leash top, leash bottom,
river, and optional overrides for the auto-computed GEX levels
(call resistance / put support).

Backed by a plain JSON file so both stock_scanner.py (terminal) and
scanner_dashboard.py (browser) read/write the same values -- edit a
level in the dashboard and the terminal scanner's log will reflect it
too.

Manual entry always wins over an auto-computed value when both exist.
"""

import json
import os

LEVELS_FILE = "levels_overrides.json"

# leash_top / leash_bottom / river have no automatic source -- manual only.
# call_resistance / put_support can come from stock_scanner.get_gex_levels()
# OR be manually overridden here.
FIELDS = ["leash_top", "leash_bottom", "river", "call_resistance", "put_support"]

LABELS = {
    "leash_top": "Leash Top",
    "leash_bottom": "Leash Bottom",
    "river": "River",
    "call_resistance": "Call Resistance (GEX)",
    "put_support": "Put Support (GEX)",
}


def load_overrides() -> dict:
    if os.path.exists(LEVELS_FILE):
        try:
            with open(LEVELS_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_override(ticker: str, field: str, value) -> dict:
    """value=None clears the manual override for that field/ticker."""
    if field not in FIELDS:
        raise ValueError(f"Unknown level field: {field}")
    data = load_overrides()
    data.setdefault(ticker, {})
    if value is None:
        data[ticker].pop(field, None)
        if not data[ticker]:
            data.pop(ticker)
    else:
        data[ticker][field] = value
    with open(LEVELS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    return data


def get_effective_levels(ticker: str, auto_values: dict) -> dict:
    """
    auto_values: dict of any auto-computed fields, e.g.
        {"call_resistance": 152.0, "put_support": 144.0}
    Returns {field: {"value": float|None, "source": "manual"|"auto"|None}}
    for ALL fields in FIELDS, so callers don't need to special-case
    fields with no auto source (leash_top/bottom, river).
    """
    overrides = load_overrides().get(ticker, {})
    effective = {}
    for f in FIELDS:
        if f in overrides and overrides[f] not in (None, ""):
            effective[f] = {"value": overrides[f], "source": "manual"}
        elif auto_values.get(f) is not None:
            effective[f] = {"value": auto_values[f], "source": "auto"}
        else:
            effective[f] = {"value": None, "source": None}
    return effective

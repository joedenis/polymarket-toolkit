"""Compare field names in the live captures against the synthetic test fixtures."""

import json
from pathlib import Path

FIX = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def events(msgs):
    """Flatten: a message may be one event or a list of events."""
    for m in msgs:
        for ev in m if isinstance(m, list) else [m]:
            if isinstance(ev, dict):
                yield ev


def compare(label, live: dict, fixture: dict):
    live_keys, fix_keys = set(live), set(fixture)
    print(f"\n== {label}")
    print("  only in fixture:", sorted(fix_keys - live_keys) or "-")
    print("  only in live:   ", sorted(live_keys - fix_keys) or "-")


live = list(events(load("live_ws_messages.json")))
first = {}
for ev in live:
    first.setdefault(ev.get("event_type"), ev)

# WebSocket: top-level fields
compare("book", first["book"], load("ws_book_array.json")[0])
compare("price_change", first["price_change"], load("ws_price_change.json"))

# WebSocket: fields inside each price_changes entry
compare(
    "price_change entry",
    first["price_change"]["price_changes"][0],
    load("ws_price_change.json")["price_changes"][0],
)

# Data API: one activity row
live_rows = load("live_activity_page.json").get("data", [])
if live_rows:
    compare("activity row", live_rows[0], load("activity_page1.json")["data"][0])
else:
    print("\n== activity row: no rows in live capture")

print("\nlive book event, for reference:")
print(json.dumps(first["book"], indent=2)[:1200])
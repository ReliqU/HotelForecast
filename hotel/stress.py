"""Prospective synthetic challenge: change the world, not the fitted models."""
from contextlib import closing
import sqlite3

from .features import FeatureStore
from .sources import DAY, days, rng, room_feed, scheduled
from .storage import normalize_rooms, save_events

SCENARIOS = {
    "control": ("Без сдвига", False, False),
    "late": ("Поздние анонсы", False, True),
    "weak": ("Слабее эффект событий", True, False),
    "both": ("Оба изменения", True, True),
}


def scenario_sets(db, cfg, anchor):
    spec = cfg["stress"]
    h = cfg["horizon"]
    end = anchor + spec["days"]*DAY
    dates = list(days(anchor+DAY, end))
    origins = list(days(anchor, end-h*DAY))[::h]
    hotels = {p["id"]: p["rooms"] for p in cfg["hotels"]}
    facts = {weak: [row for d in dates for row in normalize_rooms(
        room_feed(cfg, d, event_scale=spec["event_scale"] if weak else 1.),d,hotels)[0]]
        for weak in (False,True)}
    result = {}
    for key, (label, weak, late) in SCENARIOS.items():
        with closing(sqlite3.connect(":memory:")) as temp:
            temp.row_factory = sqlite3.Row
            db.backup(temp)
            shifted = eligible = 0
            events = []
            for d in dates:
                for e in scheduled(cfg["seed"], d):
                    if e["published"] <= anchor:
                        continue  # Never rewrite notices already known at the freeze.
                    eligible += 1
                    if late and rng(cfg["seed"],d,e["id"]).random() < spec["late_share"]:
                        new_date = max(e["published"],d-spec["notice_days"]*DAY)
                        shifted += new_date != e["published"]
                        e["published"] = new_date
                    events.append(e)
            with temp:
                temp.execute("DELETE FROM rooms WHERE day>?", (str(anchor),))
                temp.execute("DELETE FROM events WHERE source='event_html' AND published>?", (str(anchor),))
                temp.executemany("INSERT INTO rooms VALUES (?,?,?,?,?)",facts[weak])
                save_events(temp,"event_html",events)
            fs = FeatureStore(temp,cfg,end)
            result[key] = (fs.dataset(origins,h,end),
                           dict(label=label, shifted=shifted, eligible=eligible,
                                weak=weak, late=late, targets={}))
            fs.state.cache_clear()
    return result, dict(frozen_through=str(anchor), start=str(anchor+DAY), end=str(end),
                        origins=len(origins), spec=spec)

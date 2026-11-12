"""Independent chronological synthetic audit and paired error diagnostics."""

import sqlite3
from contextlib import closing

import numpy as np

from .features import FeatureStore
from .models import scores
from .sources import DAY, days, room_feed, scheduled
from .storage import normalize_rooms, save_events


def promote(audit):
    """The confirmation period is a release gate, not a search."""
    old, new = audit["previous"], audit["candidate"]
    return new["mae"] < old["mae"] and new["rmse"] <= 1.05 * old["rmse"]


def audit_datasets(db, cfg, anchor):
    horizon = cfg["horizon"]
    bridge_end = anchor + cfg["stress"]["days"] * DAY
    audit_end = bridge_end + cfg["audit_days"] * DAY
    end = audit_end + cfg["confirmation_days"] * DAY
    hotels = {h["id"]: h["rooms"] for h in cfg["hotels"]}
    with closing(sqlite3.connect(":memory:")) as tmp:
        tmp.row_factory = sqlite3.Row
        db.backup(tmp)
        with tmp:
            tmp.execute("DELETE FROM rooms WHERE day>?", (str(anchor),))
            tmp.execute(
                "DELETE FROM events WHERE source='event_html' AND published>?", (str(anchor),)
            )
            for day in days(anchor + DAY, end):
                rows, _ = normalize_rooms(room_feed(cfg, day), day, hotels)
                tmp.executemany("INSERT INTO rooms VALUES (?,?,?,?,?)", rows)
                save_events(
                    tmp,
                    "event_html",
                    [e for e in scheduled(cfg["seed"], day) if e["published"] > anchor],
                )
        fs = FeatureStore(tmp, cfg, end)
        output = []
        for first, last in ((bridge_end, audit_end), (audit_end, end)):
            origins = list(days(first, last - horizon * DAY))[::horizon]
            data = fs.dataset(origins, horizon, last)
            # Post-event information is diagnostic only, never passed to a model.
            notices = {
                (o, d): (
                    len(fs.available_events(o, d, "event_html")),
                    len(fs.available_events(d, d, "event_html")),
                )
                for o, _, d in data[3]
            }
            output.append(
                (
                    data,
                    notices,
                    dict(
                        start=str(first + DAY),
                        end=str(last),
                        frozen_through=str(anchor),
                        origins=len(origins),
                        targets={},
                    ),
                )
            )
        fs.state.cache_clear()
    return output


def diagnostics(actual, candidate, previous, forecast_dates, notices):
    """Compare forecasts for the same hotel, issue date, and target date."""
    candidate_errors = np.abs(candidate - actual)
    improvement = np.abs(previous - actual) - candidate_errors

    def metrics(mask):
        return dict(
            previous=scores(actual[mask], previous[mask]),
            candidate=scores(actual[mask], candidate[mask]),
        )

    lead = np.array([(day - origin).days for origin, _, day in forecast_dates])
    origins = [origin for origin, _, _ in forecast_dates]
    windows_better = 0
    for origin in sorted(set(origins)):
        window = [issued == origin for issued in origins]
        windows_better += float(np.mean(improvement[window])) > 1e-10
    worst_index = int(np.argmax(candidate_errors))
    worst_origin, worst_hotel, worst_day = forecast_dates[worst_index]
    known, total = notices[worst_origin, worst_day]
    return dict(
        **metrics(slice(None)),
        by_hotel={
            hotel_id: metrics([hotel == hotel_id for _, hotel, _ in forecast_dates])
            for hotel_id in dict.fromkeys(hotel for _, hotel, _ in forecast_dates)
        },
        by_horizon={"1–7": metrics(lead <= 7), "8–14": metrics(lead > 7)},
        windows_better=windows_better,
        worst=dict(
            hotel=worst_hotel,
            origin=str(worst_origin),
            date=str(worst_day),
            lead=(worst_day - worst_origin).days,
            fact=float(actual[worst_index]),
            candidate=float(candidate[worst_index]),
            previous=float(previous[worst_index]),
            error=float(candidate_errors[worst_index]),
            known_events=known,
            later_events=total - known,
        ),
    )

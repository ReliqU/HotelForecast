"""One point-in-time feature builder shared by backtests and live forecasts."""

import math
from bisect import bisect_right
from collections import defaultdict
from datetime import date
from functools import lru_cache
from statistics import median

import numpy as np

from .sources import DAY, KINDS, days, proximity

TARGETS = ("occupancy", "adr")
LABELS = {"occupancy": "Загрузка", "adr": "Средняя цена"}
UNITS = {"occupancy": "п. п.", "adr": "₽"}
SEASONAL_LAST = "тот же день недели"
SEASONAL_MEDIAN = "медиана 4 одноимённых дней"


class FeatureStore:
    def __init__(self, db, cfg, through):
        self.through = through
        self.hotels = {h["id"]: h for h in cfg["hotels"]}
        self.start = date.fromisoformat(cfg["start"])
        self.facts = defaultdict(dict)
        self.known = defaultdict(list)
        self.events = defaultdict(list)
        self.names = (
            ["Горизонт", "Синус года", "Косинус года"]
            + [f"День недели {i + 1}" for i in range(7)]
            + [f"Гостиница {h['name']}" for h in cfg["hotels"]]
            + [
                f"{LABELS[t]}: {s}"
                for t in TARGETS
                for s in (
                    "последняя",
                    "лаг 7",
                    "лаг 14",
                    "лаг 28",
                    "средняя 7 дней",
                    "средняя 28 дней",
                    SEASONAL_LAST,
                    SEASONAL_MEDIAN,
                )
            ]
            + ["Музыка", "Выставки", "Спорт", "Другие события", "Близость площадок"]
        )
        self.event_start = len(self.names) - 5
        for r in db.execute("SELECT * FROM rooms WHERE day<=? ORDER BY day", (str(through),)):
            d, h = date.fromisoformat(r["day"]), r["hotel"]
            occ = 100 * r["sold"] / r["available"] if r["sold"] is not None else None
            adr = r["revenue"] / r["sold"] if r["sold"] and r["revenue"] is not None else None
            self.facts[h][d] = (occ, adr)
            for i, v in enumerate((occ, adr)):
                if v is not None:
                    self.known[h, i].append((d, v))
        for r in db.execute(
            "SELECT e.*, p.url, p.basis FROM events AS e "
            "LEFT JOIN event_provenance AS p USING(source,id) WHERE published<=?",
            (str(through),),
        ):
            e = dict(r)
            e["published"] = date.fromisoformat(e["published"])
            self.events[e["source"], date.fromisoformat(e["day"])].append(e)

    def fact(self, hotel, target_day, i):
        return self.facts[hotel].get(target_day, (None, None))[i]

    def last(self, hotel, i, d):
        series = self.known[hotel, i]
        ix = bisect_right(series, (d, float("inf")))
        if ix:
            return series[ix - 1][1]
        # Fixed priors only when no earlier fact exists.
        return (60.0, self.hotels[hotel]["rate"])[i]

    @lru_cache(maxsize=24000)
    def state(self, hotel, origin):
        result = []
        for i in range(len(TARGETS)):
            result.append(
                [self.last(hotel, i, origin - n * DAY) for n in (0, 7, 14, 28)]
                + [
                    sum(self.last(hotel, i, origin - j * DAY) for j in range(w)) / w
                    for w in (7, 28)
                ]
            )
        return result

    def available_events(self, origin, d, source):
        return [e for e in self.events[source, d] if e["published"] <= origin]

    def seasonal(self, hotel, origin, target_day, i):
        same = origin - ((origin.weekday() - target_day.weekday()) % 7) * DAY
        last = self.last(hotel, i, same)
        observations = [self.fact(hotel, same - 7 * j * DAY, i) for j in range(4)]
        values = [v for v in observations if v is not None]
        return last, median(values) if values else last

    def row(self, hotel, origin, d, source="event_html"):
        if not self.start <= origin <= self.through or d <= origin:
            raise ValueError("future target and available origin are required")
        angle = 2 * math.pi * (d.timetuple().tm_yday - 1) / 365.25
        seasonal = [self.seasonal(hotel, origin, d, i) for i in range(len(TARGETS))]
        history = [v for i, xs in enumerate(self.state(hotel, origin)) for v in (*xs, *seasonal[i])]
        ev = self.available_events(origin, d, source)
        event_values = [sum(e["kind"] == k for e in ev) for k in KINDS]
        event_values.append(sum(proximity(self.hotels[hotel], e) for e in ev))
        return (
            [float((d - origin).days), math.sin(angle), math.cos(angle)]
            + [float(d.weekday() == j) for j in range(7)]
            + [float(hotel == h) for h in self.hotels]
            + history
            + event_values
        ), [v[0] for v in seasonal]

    def dataset(self, origins, horizon, cutoff):
        x, y, base, meta = [], [], [], []
        for origin in origins:
            for h in self.hotels:
                for d in days(origin + DAY, origin + horizon * DAY):
                    if d > cutoff:
                        raise ValueError("label crosses split boundary")
                    row, b = self.row(h, origin, d)
                    x.append(row)
                    y.append(self.facts[h].get(d, (None, None)))
                    base.append(b)
                    meta.append((origin, h, d))
        return (
            np.asarray(x, dtype=float),
            np.asarray(y, dtype=float),
            np.asarray(base, dtype=float),
            meta,
        )

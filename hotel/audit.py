"""Independent chronological synthetic audit and paired error diagnostics."""
from contextlib import closing
import sqlite3

import numpy as np

from .features import FeatureStore
from .models import scores
from .sources import DAY, days, room_feed, scheduled
from .storage import normalize_rooms, save_events


def promote(audit):
    """The confirmation period is a release gate, not a search."""
    old,new = audit["previous"],audit["candidate"]
    return new["mae"] < old["mae"] and new["rmse"] <= 1.05*old["rmse"]


def audit_datasets(db, cfg, anchor):
    horizon = cfg["horizon"]
    bridge_end = anchor + cfg["stress"]["days"]*DAY
    audit_end = bridge_end + cfg["audit_days"]*DAY
    end = audit_end + cfg["confirmation_days"]*DAY
    hotels = {h["id"]:h["rooms"] for h in cfg["hotels"]}
    with closing(sqlite3.connect(":memory:")) as tmp:
        tmp.row_factory = sqlite3.Row
        db.backup(tmp)
        with tmp:
            tmp.execute("DELETE FROM rooms WHERE day>?",(str(anchor),))
            tmp.execute("DELETE FROM events WHERE source='event_html' AND published>?",(str(anchor),))
            for day in days(anchor+DAY,end):
                rows,_ = normalize_rooms(room_feed(cfg,day),day,hotels)
                tmp.executemany("INSERT INTO rooms VALUES (?,?,?,?,?)",rows)
                save_events(tmp,"event_html",[e for e in scheduled(cfg["seed"],day) if e["published"]>anchor])
        fs = FeatureStore(tmp,cfg,end)
        output = []
        for first,last in ((bridge_end,audit_end),(audit_end,end)):
            origins = list(days(first,last-horizon*DAY))[::horizon]
            data = fs.dataset(origins,horizon,last)
            # Post-event information is diagnostic only, never passed to a model.
            notices = {(o,d):(len(fs.available_events(o,d,"event_html")),
                               len(fs.available_events(d,d,"event_html")))
                       for o,_,d in data[3]}
            output.append((data,notices,dict(start=str(first+DAY),end=str(last),frozen_through=str(anchor),
                                              origins=len(origins),targets={})))
        fs.state.cache_clear()
    return output


def diagnostics(y, candidate, previous, meta, notices):
    gap = np.abs(previous-y)-np.abs(candidate-y)
    def metrics(mask):
        return dict(previous=scores(y[mask],previous[mask]),candidate=scores(y[mask],candidate[mask]))
    lead = np.array([(d-o).days for o,_,d in meta])
    indices = sorted({o for o,_,_ in meta})
    win = sum(float(np.mean(gap[[o==start for o,_,_ in meta]]))>1e-10 for start in indices)
    i = int(np.argmax(np.abs(candidate-y)))
    o,h,d = meta[i]
    known, total = notices[o,d]
    return dict(
        **metrics(slice(None)),
        by_hotel={h:metrics([hotel==h for _,hotel,_ in meta]) for h in dict.fromkeys(h for _,h,_ in meta)},
        by_horizon={"1–7":metrics(lead<=7),"8–14":metrics(lead>7)},
        windows_better=win,
        worst=dict(hotel=h,origin=str(o),date=str(d),lead=(d-o).days,
                   fact=float(y[i]),candidate=float(candidate[i]),previous=float(previous[i]),
                   error=float(abs(candidate[i]-y[i])),known_events=known,later_events=total-known))

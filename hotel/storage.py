"""SQLite raw layer, validated facts and incremental loading journal."""
import csv
import hashlib
import io
import json
import math
import sqlite3
from datetime import date

from .sources import DAY, days, fetch_events, html_feed, parse_html, parse_public, room_feed

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS raw (
  source TEXT, slice TEXT, sha256 TEXT NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY(source, slice)
);
CREATE TABLE IF NOT EXISTS rooms (
  hotel TEXT, day TEXT, available INTEGER NOT NULL, sold INTEGER, revenue REAL,
  PRIMARY KEY(hotel, day), CHECK(available>0), CHECK(sold IS NULL OR sold BETWEEN 0 AND available),
  CHECK(revenue IS NULL OR revenue>=0)
);
CREATE TABLE IF NOT EXISTS events (
  source TEXT, id TEXT, day TEXT NOT NULL, published TEXT NOT NULL,
  title TEXT NOT NULL, venue TEXT, kind TEXT NOT NULL, lat REAL, lon REAL,
  PRIMARY KEY(source, id)
);
CREATE INDEX IF NOT EXISTS events_day ON events (source, day, published);
CREATE TABLE IF NOT EXISTS loads (
  id INTEGER PRIMARY KEY, run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  source TEXT NOT NULL, through_day TEXT NOT NULL, status TEXT NOT NULL,
  added_slices INTEGER NOT NULL, rows INTEGER NOT NULL, flagged INTEGER NOT NULL,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY, data_hash TEXT NOT NULL, target TEXT NOT NULL, family TEXT NOT NULL,
  params TEXT NOT NULL, seed INTEGER NOT NULL, cutoff TEXT NOT NULL, metrics TEXT NOT NULL,
  version TEXT NOT NULL
);
"""


def connect(path, cfg):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    key = json.dumps([cfg["start"], cfg["seed"], cfg["hotels"]], sort_keys=True, ensure_ascii=False)
    row = db.execute("SELECT value FROM settings WHERE key='generator'").fetchone()
    if row and row[0] != key:
        db.close()
        raise ValueError("Источники изменились. Укажите новую БД через --db.")
    with db:
        db.execute("INSERT OR IGNORE INTO settings VALUES ('generator', ?)", (key,))
    return db


def normalize_rooms(payload, day, hotels):
    rows, bad, seen = [], 0, set()
    for r in csv.DictReader(io.StringIO(payload)):
        try:
            h, available, sold = r["hotel"], int(r["available"]), int(r["sold"])
            if h not in hotels or h in seen or r["day"] != str(day) or not 0 < available <= hotels[h]:
                raise ValueError("invalid identity or capacity")
            seen.add(h)
            rev = float(r["revenue"]) if r["revenue"] else None
            if not 0 <= sold <= available:
                sold = rev = None
                bad += 1
            elif rev is None or not math.isfinite(rev) or rev < 0 or (sold == 0 and rev != 0):
                rev = None
                bad += 1
            rows.append((h, str(day), available, sold, rev))
        except (KeyError, ValueError, TypeError):
            bad += 1
    return rows, bad


def save_events(db, source, events):
    db.executemany("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?)", [
        (source, e["id"], str(e["date"]), str(e["published"]), e["title"], e["venue"],
         e["kind"], e["lat"], e["lon"]) for e in events
    ])


def save_raw(db, source, d, text):
    db.execute("INSERT INTO raw VALUES (?,?,?,?)",
               (source, str(d), hashlib.sha256(text.encode()).hexdigest(), text))


def log(db, source, end, status, slices, rows, flagged, detail=None):
    db.execute("INSERT INTO loads(source,through_day,status,added_slices,rows,flagged,detail) "
               "VALUES(?,?,?,?,?,?,?)", (source, str(end), status, slices, rows, flagged, detail))


def ingest(db, cfg, end):
    start = date.fromisoformat(cfg["start"])
    hotels = {h["id"]: h["rooms"] for h in cfg["hotels"]}
    for source, begin, generator in (("pms_csv", start, room_feed), ("event_html", start - 34*DAY, html_feed)):
        slices = rows = flagged = 0
        try:
            with db:
                done = {r[0] for r in db.execute("SELECT slice FROM raw WHERE source=?", (source,))}
                for d in days(begin, end):
                    if str(d) in done:
                        continue
                    payload = generator(cfg, d)
                    save_raw(db, source, d, payload)
                    if source == "pms_csv":
                        parsed, rejected = normalize_rooms(payload, d, hotels)
                        db.executemany("INSERT INTO rooms VALUES (?,?,?,?,?)", parsed)
                    else:
                        parsed, rejected = parse_html(payload, d)
                        save_events(db, source, parsed)
                    slices, rows, flagged = slices+1, rows+len(parsed), flagged+rejected
                log(db, source, end, "ok", slices, rows, flagged)
        except Exception as e:
            with db:
                log(db, source, end, "failed", 0, 0, 0, f"{type(e).__name__}: {e}")
            raise
    return journal(db, 2)[::-1]


def ingest_public(db, cfg, today):
    source = "kudago_json"
    try:
        if db.execute("SELECT 1 FROM raw WHERE source=? AND slice=?", (source, str(today))).fetchone():
            with db:
                log(db, source, today, "ok", 0, 0, 0, "cached daily snapshot")
            return True
        first, last = today + DAY, today + cfg["horizon"]*DAY
        data = fetch_events(cfg, first, last)
        events, bad = parse_public(data, today, first, last)
        with db:
            save_raw(db, source, today, json.dumps(data, ensure_ascii=False))
            save_events(db, source, events)
            log(db, source, today, "ok", 1, len(events), bad)
        return True
    except Exception as e:
        with db:
            log(db, source, today, "failed", 0, 0, 0, f"{type(e).__name__}: {e}")
        return False


def journal(db, limit=12):
    return [dict(r) for r in db.execute("SELECT * FROM loads ORDER BY id DESC LIMIT ?", (limit,))]


def fingerprint(db, cutoff):
    h = hashlib.sha256()
    for r in db.execute("SELECT source,slice,sha256 FROM raw WHERE source IN ('pms_csv','event_html') "
                        "AND slice<=? ORDER BY source,slice", (str(cutoff),)):
        h.update((":".join(r) + "\n").encode())
    return h.hexdigest()

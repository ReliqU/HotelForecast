"""SQLite raw layer, validated facts and incremental loading journal."""

import csv
import hashlib
import io
import json
import math
import sqlite3
from datetime import date

from .archive import ARCHIVE_SOURCE, parse_archive
from .sources import (
    DAY,
    days,
    html_feed,
    parse_html,
    room_feed,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS raw (
  source TEXT, slice TEXT, sha256 TEXT NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY(source, slice)
);
CREATE INDEX IF NOT EXISTS raw_source_hash ON raw (source, sha256);
CREATE TABLE IF NOT EXISTS online_heads (
  source TEXT PRIMARY KEY, slice TEXT NOT NULL, checked_at TEXT NOT NULL,
  FOREIGN KEY(source, slice) REFERENCES raw(source, slice)
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
CREATE TABLE IF NOT EXISTS event_provenance (
  source TEXT, id TEXT, url TEXT NOT NULL, basis TEXT NOT NULL, archived_on TEXT NOT NULL,
  PRIMARY KEY(source,id)
);
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
            if (
                h not in hotels
                or h in seen
                or r["day"] != str(day)
                or not 0 < available <= hotels[h]
            ):
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
    db.executemany(
        "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                source,
                e["id"],
                str(e["date"]),
                str(e["published"]),
                e["title"],
                e["venue"],
                e["kind"],
                e["lat"],
                e["lon"],
            )
            for e in events
        ],
    )


def save_raw(db, source, d, text):
    db.execute(
        "INSERT INTO raw VALUES (?,?,?,?)",
        (source, str(d), hashlib.sha256(text.encode()).hexdigest(), text),
    )


def save_online_snapshot(db, source, checked_at, payload):
    """Store the content once and track when the source last returned it."""
    digest = hashlib.sha256(payload.encode()).hexdigest()
    # Distinct responses can arrive on the same clock tick.
    slice_key = f"{checked_at}#{digest}"
    added = db.execute(
        "INSERT INTO raw (source,slice,sha256,payload) "
        "SELECT ?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM raw WHERE source=? AND sha256=?)",
        (source, slice_key, digest, payload, source, digest),
    ).rowcount
    stored = db.execute(
        "SELECT slice FROM raw WHERE source=? AND sha256=? ORDER BY rowid DESC LIMIT 1",
        (source, digest),
    ).fetchone()[0]
    db.execute(
        "INSERT INTO online_heads VALUES (?,?,?) ON CONFLICT(source) DO UPDATE SET "
        "slice=excluded.slice, checked_at=excluded.checked_at "
        "WHERE excluded.checked_at>=online_heads.checked_at",
        (source, stored, checked_at),
    )
    return added


def latest_online_snapshot(db, source):
    result = db.execute(
        "SELECT raw.payload, online_heads.checked_at FROM online_heads "
        "JOIN raw USING(source,slice) WHERE source=?",
        (source,),
    ).fetchone()
    if result is not None:
        return result
    # Databases made before online_heads still hold their original snapshots.
    return db.execute(
        "SELECT payload,slice FROM raw WHERE source=? ORDER BY slice DESC LIMIT 1", (source,)
    ).fetchone()


def log(db, source, end, status, slices, rows, flagged, detail=None):
    db.execute(
        "INSERT INTO loads(source,through_day,status,added_slices,rows,flagged,detail) "
        "VALUES(?,?,?,?,?,?,?)",
        (source, str(end), status, slices, rows, flagged, detail),
    )


def ingest(db, cfg, end):
    start = date.fromisoformat(cfg["start"])
    hotels = {h["id"]: h["rooms"] for h in cfg["hotels"]}
    for source, begin, generator in (
        ("pms_csv", start, room_feed),
        ("event_html", start - 34 * DAY, html_feed),
    ):
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
                    slices, rows, flagged = slices + 1, rows + len(parsed), flagged + rejected
                log(db, source, end, "ok", slices, rows, flagged)
        except Exception as e:
            with db:
                log(db, source, end, "failed", 0, 0, 0, f"{type(e).__name__}: {e}")
            raise
    return journal(db, 2)[::-1]


def ingest_archive(db, path, end):
    try:
        text = path.read_text(encoding="utf-8-sig")
        snapshot, events = parse_archive(text)
        with db:
            r = db.execute(
                "SELECT sha256 FROM raw WHERE source=? AND slice=?", (ARCHIVE_SOURCE, str(snapshot))
            ).fetchone()
            if r:
                if r[0] != hashlib.sha256(text.encode()).hexdigest():
                    raise ValueError("Архив на эту дату уже загружен с другим содержимым.")
                log(db, ARCHIVE_SOURCE, end, "ok", 0, 0, 0, "cached archive")
                return True
            save_raw(db, ARCHIVE_SOURCE, snapshot, text)
            save_events(db, ARCHIVE_SOURCE, events)
            db.executemany(
                "INSERT OR IGNORE INTO event_provenance VALUES (?,?,?,?,?)",
                [(ARCHIVE_SOURCE, e["id"], e["url"], e["basis"], str(snapshot)) for e in events],
            )
            log(db, ARCHIVE_SOURCE, end, "ok", 1, len(events), 0, f"snapshot {snapshot}")
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        with db:
            log(db, ARCHIVE_SOURCE, end, "failed", 0, 0, 0, f"{type(e).__name__}: {e}")
        return False


def journal(db, limit=12):
    return [dict(r) for r in db.execute("SELECT * FROM loads ORDER BY id DESC LIMIT ?", (limit,))]


def fingerprint(db, cutoff):
    h = hashlib.sha256()
    for r in db.execute(
        "SELECT source,slice,sha256 FROM raw WHERE source IN ('pms_csv','event_html') "
        "AND slice<=? ORDER BY source,slice",
        (str(cutoff),),
    ):
        h.update((":".join(r) + "\n").encode())
    return h.hexdigest()

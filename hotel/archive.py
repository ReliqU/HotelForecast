"""Offline, auditable import of a small curated sample of public announcements."""
from datetime import date
import json
import re
from urllib.parse import urlsplit

from .sources import classify, days

ARCHIVE_SOURCE = "public_archive"


def parse_archive(payload):
    data = json.loads(payload)
    snapshot = date.fromisoformat(data["snapshot_date"])
    rows, seen = [], set()
    if not data["events"]:
        raise ValueError("Пустой архив анонсов")
    for item in data["events"]:
        key = item["id"]
        start, end = (date.fromisoformat(item[k]) for k in ("start", "end"))
        available = date.fromisoformat(item["available_from"])
        basis, url = item["availability_basis"], item["url"]
        parsed = urlsplit(url)
        title, venue, summary = (item[k].strip() for k in ("title", "venue", "summary"))
        lat, lon = item.get("lat"), item.get("lon")
        if not re.fullmatch(r"[a-z0-9-]{1,60}", key) or key in seen:
            raise ValueError(f"Неподходящий или повторный ID: {key}")
        if not (start <= end and (end-start).days <= 90 and available <= min(end, snapshot)):
            raise ValueError(f"Некорректные даты: {key}")
        if basis not in ("published", "observed") or (basis == "observed" and available != snapshot):
            raise ValueError(f"Не подтверждена дата доступности: {key}")
        if not (parsed.scheme == "https" and parsed.hostname and not parsed.username
                and not any(c.isspace() for c in url) and title and venue and summary):
            raise ValueError(f"Нет пригодного источника или описания: {key}")
        if (lat, lon) != (None, None) and not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                and -90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"Некорректные координаты: {key}")
        seen.add(key)
        for day in days(start, end):
            rows.append(dict(id=f"{key}:{day}", date=day, published=available, title=title, venue=venue,
                             kind=classify(title+" "+summary), lat=lat, lon=lon, url=url,
                             basis=basis, event_key=key, archived_on=snapshot))
    return snapshot, rows

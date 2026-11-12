"""Deterministic teaching feeds and public Moscow event sources."""

import csv
import html
import io
import json
import math
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

DAY = timedelta(days=1)
MSK = timezone(timedelta(hours=3))
USER_AGENT = "HotelForecast/1.0 (academic prototype)"
MOS_AFISHA = "https://www.mos.ru/afisha/"
KINDS = ("music", "expo", "sports", "other")
WORD = {"music": "Концерт", "expo": "Выставка", "sports": "Турнир", "other": "Фестиваль"}
VENUES = (
    ("Городская сцена", 55.754, 37.608),
    ("Выставочный павильон", 55.75, 37.55),
    ("Стадион у парка", 55.795, 37.677),
    ("Дом культуры", 55.78, 37.61),
)


def days(start, end):
    return (start + i * DAY for i in range(max(0, (end - start).days + 1)))


def rng(seed, day, salt):
    return random.Random(f"{seed}:{day.isoformat()}:{salt}")


def scheduled(seed, day):
    r = rng(seed, day, "events")
    count = sum(r.random() < p for p in (0.49, 0.21, 0.07))
    for i in range(count):
        j = r.randrange(4)
        venue, lat, lon = VENUES[j]
        yield dict(
            id=f"s-{day}-{i}",
            date=day,
            published=day - r.randint(2, 34) * DAY,
            kind=KINDS[j],
            venue=venue,
            lat=lat,
            lon=lon,
            title=f"{WORD[KINDS[j]]} «{r.choice(('Городские встречи', 'Открытый сезон', 'Пульс города', 'Новая сцена'))}»",
        )


def km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(min(1.0, a)))


def proximity(h, e):
    if e["lat"] is None:
        return 0.0
    return math.exp(-km(h["lat"], h["lon"], e["lat"], e["lon"]) / 3.8)


def room_feed(cfg, day, event_scale=1.0):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(("hotel", "day", "available", "sold", "revenue"))
    events = list(scheduled(cfg["seed"], day))
    annual = 2 * math.pi * (day.timetuple().tm_yday - 1) / 365.25
    for i, h in enumerate(cfg["hotels"]):
        r = rng(cfg["seed"], day, h["id"])
        drift = 0.022 * math.sin((day.toordinal() - 738520) / 22.0 + i)
        demand = event_scale * sum(
            proximity(h, e) * (1.2 if e["kind"] == "expo" else 1.0) for e in events
        )
        weekly = (-0.066, -0.035, -0.025, 0.01, 0.055, 0.11, 0.014)[day.weekday()]
        share = (
            0.62
            + 0.055 * math.sin(annual - 0.7)
            + weekly
            + 0.012 * i
            + drift
            + 0.22 * min(1.5, demand)
            + r.gauss(0, 0.035)
        )
        available = h["rooms"] - (r.randint(1, 5) if r.random() < 0.12 else 0)
        sold = round(max(0.04, min(0.99, share)) * available)
        rate = (
            h["rate"]
            + 620 * math.sin(annual - 0.7)
            + 3900 * weekly
            + 1050 * demand
            + 4000 * drift
            + r.gauss(0, 220)
        )
        # Rare deliberately damaged records exercise the cleaning path.
        glitch = r.random()
        revenue = round(sold * max(1000.0, rate), 2)
        if glitch < 0.002:
            revenue = ""
        elif glitch < 0.004:
            sold = available + 4
        w.writerow((h["id"], day, available, sold, revenue))
    return out.getvalue()


def html_feed(cfg, publication):
    lines = ['<!doctype html><html lang="ru"><meta charset="utf-8"><main>']
    for day in days(publication + DAY, publication + 34 * DAY):
        for e in scheduled(cfg["seed"], day):
            if e["published"] == publication:
                lines.append(
                    f'<article id="{e["id"]}"><h2>{html.escape(e["title"])}</h2>'
                    f"<p>{WORD[e['kind']]} на площадке {html.escape(e['venue'])}.</p>"
                    f'<time datetime="{day}">{day}</time>'
                    f'<address data-lat="{e["lat"]}" data-lon="{e["lon"]}">'
                    f"{html.escape(e['venue'])}</address></article>"
                )
    return "\n".join(lines + ["</main></html>"])


def classify(text):
    words = text.lower()
    for prefixes, kind in (
        (("выстав", "экспо", "форум", "exhibition"), "expo"),
        (("турнир", "матч", "спорт", "sport"), "sports"),
        (("концерт", "музык", "concert"), "music"),
    ):
        if any(p in words for p in prefixes):
            return kind
    return "other"


class EventHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current, self.field, self.rows = None, None, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "article":
            self.current = dict(id=a.get("id"), title="", venue="", description="")
        if self.current is not None:
            if tag == "time":
                self.current["date"] = a.get("datetime")
            elif tag == "address":
                self.current.update(lat=a.get("data-lat"), lon=a.get("data-lon"))
                self.field = "venue"
            elif tag in ("h2", "p"):
                self.field = "title" if tag == "h2" else "description"

    def handle_endtag(self, tag):
        if tag == "article" and self.current is not None:
            self.rows.append(self.current)
            self.current = None
        if tag in ("h2", "p", "address", "article"):
            self.field = None

    def handle_data(self, data):
        if self.current is not None and self.field:
            self.current[self.field] += data


def parse_html(payload, published):
    p = EventHTML()
    p.feed(payload)
    good, bad = [], 0
    for e in p.rows:
        try:
            d = date.fromisoformat(e["date"])
            lat, lon = float(e["lat"]), float(e["lon"])
            if (
                not e["id"]
                or not e["title"].strip()
                or d < published
                or not (-90 <= lat <= 90 and -180 <= lon <= 180)
            ):
                raise ValueError("invalid event")
            good.append(
                dict(
                    id=e["id"],
                    date=d,
                    published=published,
                    title=e["title"],
                    venue=e["venue"],
                    lat=lat,
                    lon=lon,
                    kind=classify(e["title"] + " " + e["description"]),
                )
            )
        except (ValueError, KeyError, TypeError):
            bad += 1
    return good, bad


def request_html(url, timeout):
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
        limit = 2_000_000
        content = response.read(limit + 1)
        if len(content) > limit:
            raise ValueError("Страница больше допустимого размера.")
        return content.decode(response.headers.get_content_charset() or "utf-8")


def request_json(url, cfg):
    for n in range(cfg["request_retries"]):
        try:
            with urlopen(
                Request(url, headers={"User-Agent": USER_AGENT}),
                timeout=cfg["request_timeout"],
            ) as r:
                return json.load(r)
        except HTTPError as err:
            if err.code not in (408, 429, 500, 502, 503, 504) or n + 1 == cfg["request_retries"]:
                raise
            delay = err.headers.get("Retry-After", "")
            time.sleep(min(10, int(delay)) if delay.isdecimal() else 2**n)
        except (URLError, TimeoutError):
            if n + 1 == cfg["request_retries"]:
                raise
            time.sleep(2**n)
    raise RuntimeError("no request attempts configured")


def fetch_events(cfg, start, end, max_pages=2):
    def midnight_timestamp(day):
        return int(datetime.combine(day, datetime.min.time(), MSK).timestamp())

    base = cfg["event_url"]
    url = (
        base
        + "?"
        + urlencode(
            dict(
                location="msk",
                page_size=100,
                fields="id,publication_date,dates,title,description,place,site_url",
                expand="place",
                actual_since=midnight_timestamp(start),
                actual_until=midnight_timestamp(end + DAY) - 1,
            )
        )
    )
    results = []
    for _ in range(max_pages):
        data = request_json(url, cfg)
        results.extend(data["results"])
        url = data.get("next")
        if not url:
            return results
        if urlparse(url).netloc != urlparse(base).netloc or urlparse(url).scheme != "https":
            raise ValueError("unexpected pagination URL")
    return results


def parse_public(items, snapshot, start, end):
    """Availability = first local observation, never just the API publication timestamp."""

    def local_date(timestamp):
        return datetime.fromtimestamp(int(timestamp), MSK).date()

    good, bad = {}, 0
    for item in items:
        try:
            published = local_date(item["publication_date"])
            place = item.get("place") or {}
            coords = place.get("coords") or {}
            lat, lon = coords.get("lat"), coords.get("lon")
            if not (
                isinstance(lat, (int, float))
                and isinstance(lon, (int, float))
                and -90 <= lat <= 90
                and -180 <= lon <= 180
            ):
                lat = lon = None
            title = item["title"].strip()
            if not title or published > snapshot:
                raise ValueError("invalid event")
            for dt in item["dates"]:
                first, last = local_date(dt["start"]), local_date(dt.get("end") or dt["start"])
                for d in days(max(start, first), min(end, last)):
                    eid = f"k-{item['id']}-{d}"
                    text = html.unescape(re.sub(r"<[^>]*>", " ", item.get("description") or ""))
                    good[eid] = dict(
                        id=eid,
                        date=d,
                        published=max(published, snapshot),
                        title=title,
                        venue=place.get("title", ""),
                        lat=lat,
                        lon=lon,
                        kind=classify(title + " " + text),
                        url=item.get("site_url"),
                        basis="observed",
                    )
        except (ValueError, KeyError, TypeError, AttributeError, OSError, OverflowError):
            bad += 1
    return list(good.values()), bad


def fetch_mos_afisha(cfg, start, end):
    page = request_html(MOS_AFISHA, cfg["request_timeout"])
    embedded = re.search(r'<script\b[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if embedded is None:
        raise ValueError("На странице Мосбилета не найден список событий.")
    props = json.loads(embedded[1])["props"]["pageProps"]
    queries = props["dehydratedState"]["queries"]
    return [
        event
        for query in queries
        for block in query["state"]["data"].get("fullBlocks", {}).values()
        for event in block.get("events", [])
    ]


def parse_mos_afisha(items, snapshot, start, end):
    events = {}
    rejected = 0
    for item in items:
        try:
            # The listing gives the first and last show, not a daily schedule.
            day = datetime.fromisoformat(item["dateFrom"]).date()
            if not start <= day <= end:
                continue
            title = item["title"].strip()
            if not title:
                raise ValueError("Пустое название события.")
            latitude, longitude = item.get("venueLat"), item.get("venueLon")
            try:
                latitude, longitude = float(latitude), float(longitude)
                if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                    raise ValueError
            except (TypeError, ValueError):
                latitude = longitude = None
            path = item.get("url") or ""
            url = (
                urljoin("https://bilet.mos.ru/", path) if path.startswith("/event/") else MOS_AFISHA
            )
            event_id = f"mos-{item['id']}-{day}"
            events[event_id] = dict(
                id=event_id,
                date=day,
                published=snapshot,
                title=title,
                venue=item.get("venueTitle") or "Москва",
                lat=latitude,
                lon=longitude,
                kind=classify(f"{title} {item.get('sphere') or ''}"),
                url=url,
                basis="observed",
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            rejected += 1
    return list(events.values()), rejected

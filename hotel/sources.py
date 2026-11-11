"""Deterministic offline source simulators and an optional KudaGo adapter."""
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
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

DAY = timedelta(days=1)
MSK = timezone(timedelta(hours=3))
KINDS = ("music", "expo", "sports", "other")
WORD = {"music": "Концерт", "expo": "Выставка", "sports": "Турнир", "other": "Фестиваль"}
VENUES = (("Городская сцена", 55.754, 37.608), ("Выставочный павильон", 55.75, 37.55),
          ("Стадион у парка", 55.795, 37.677), ("Дом культуры", 55.78, 37.61))


def days(start, end):
    return (start + i * DAY for i in range(max(0, (end - start).days + 1)))


def rng(seed, day, salt):
    return random.Random(f"{seed}:{day.isoformat()}:{salt}")


def scheduled(seed, day):
    r = rng(seed, day, "events")
    count = sum(r.random() < p for p in (.49, .21, .07))
    for i in range(count):
        j = r.randrange(4)
        venue, lat, lon = VENUES[j]
        yield dict(id=f"s-{day}-{i}", date=day, published=day - r.randint(2, 34) * DAY,
                   kind=KINDS[j], venue=venue, lat=lat, lon=lon,
                   title=f"{WORD[KINDS[j]]} «{r.choice(('Городские встречи', 'Открытый сезон', 'Пульс города', 'Новая сцена'))}»")


def km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2-p1)/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(math.radians(lon2-lon1)/2)**2
    return 6371 * 2 * math.asin(math.sqrt(min(1., a)))


def proximity(h, e):
    if e["lat"] is None:
        return 0.
    return math.exp(-km(h["lat"], h["lon"], e["lat"], e["lon"]) / 3.8)


def room_feed(cfg, day):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(("hotel", "day", "available", "sold", "revenue"))
    events = list(scheduled(cfg["seed"], day))
    annual = 2 * math.pi * (day.timetuple().tm_yday - 1) / 365.25
    for i, h in enumerate(cfg["hotels"]):
        r = rng(cfg["seed"], day, h["id"])
        drift = 0.022 * math.sin((day.toordinal() - 738520) / 22.0 + i)
        demand = sum(proximity(h, e) * (1.2 if e["kind"] == "expo" else 1.) for e in events)
        weekly = (-.066, -.035, -.025, .01, .055, .11, .014)[day.weekday()]
        share = (.62 + .055 * math.sin(annual - .7) + weekly + .012 * i + drift
                 + .22 * min(1.5, demand) + r.gauss(0, .035))
        available = h["rooms"] - (r.randint(1, 5) if r.random() < .12 else 0)
        sold = round(max(.04, min(.99, share)) * available)
        rate = h["rate"] + 620 * math.sin(annual - .7) + 3900 * weekly + 1050 * demand + 4000*drift + r.gauss(0, 220)
        # Rare deliberately damaged records exercise the cleaning path.
        glitch = r.random()
        revenue = round(sold * max(1000., rate), 2)
        if glitch < .002:
            revenue = ""
        elif glitch < .004:
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
                    f'<p>{WORD[e["kind"]]} на площадке {html.escape(e["venue"])}.</p>'
                    f'<time datetime="{day}">{day}</time>'
                    f'<address data-lat="{e["lat"]}" data-lon="{e["lon"]}">'
                    f'{html.escape(e["venue"])}</address></article>'
                )
    return "\n".join(lines + ["</main></html>"])


def classify(text):
    words = text.lower()
    for prefixes, kind in ((("выстав", "экспо", "форум", "exhibition"), "expo"),
                           (("турнир", "матч", "спорт", "sport"), "sports"),
                           (("концерт", "музык", "concert"), "music")):
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
            if not e["id"] or not e["title"].strip() or d < published or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("invalid event")
            good.append(dict(id=e["id"], date=d, published=published, title=e["title"], venue=e["venue"],
                             lat=lat, lon=lon, kind=classify(e["title"]+" "+e["description"])))
        except (ValueError, KeyError, TypeError):
            bad += 1
    return good, bad


def request_json(url, cfg):
    for n in range(cfg["request_retries"]):
        try:
            with urlopen(Request(url, headers={"User-Agent": "HotelForecast/1.0 (academic prototype)"}),
                         timeout=cfg["request_timeout"]) as r:
                return json.load(r)
        except HTTPError as err:
            if err.code not in (408, 429, 500, 502, 503, 504) or n+1 == cfg["request_retries"]:
                raise
            delay = err.headers.get("Retry-After", "")
            time.sleep(min(10, int(delay)) if delay.isdecimal() else 2**n)
        except (URLError, TimeoutError):
            if n+1 == cfg["request_retries"]:
                raise
            time.sleep(2**n)
    raise RuntimeError("no request attempts configured")


def fetch_events(cfg, start, end, max_pages=10):
    stamp = lambda d: int(datetime.combine(d, datetime.min.time(), MSK).timestamp())
    base = cfg["event_url"]
    url = base + "?" + urlencode(dict(location="msk", page_size=100, fields=
        "id,publication_date,dates,title,description,place,site_url", expand="place",
        actual_since=stamp(start), actual_until=stamp(end + DAY) - 1))
    results = []
    for _ in range(max_pages):
        data = request_json(url, cfg)
        results.extend(data["results"])
        url = data.get("next")
        if not url:
            return results
        if urlparse(url).netloc != urlparse(base).netloc or urlparse(url).scheme != "https":
            raise ValueError("unexpected pagination URL")
    raise ValueError("pagination limit; narrow the request interval")


def parse_public(items, snapshot, start, end):
    """Availability = first local observation, never just the API publication timestamp."""
    good, bad = {}, 0
    as_date = lambda stamp: datetime.fromtimestamp(int(stamp), MSK).date()
    for item in items:
        try:
            published = as_date(item["publication_date"])
            place = item.get("place") or {}
            coords = place.get("coords") or {}
            lat, lon = coords.get("lat"), coords.get("lon")
            if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                    and -90 <= lat <= 90 and -180 <= lon <= 180):
                lat = lon = None
            title = item["title"].strip()
            if not title or published > snapshot:
                raise ValueError("invalid event")
            for dt in item["dates"]:
                first, last = as_date(dt["start"]), as_date(dt.get("end") or dt["start"])
                for d in days(max(start, first), min(end, last)):
                    eid = f'k-{item["id"]}-{d}'
                    text = html.unescape(re.sub(r"<[^>]*>", " ", item.get("description") or ""))
                    good[eid] = dict(id=eid, date=d, published=max(published, snapshot), title=title,
                                     venue=place.get("title", ""), lat=lat, lon=lon,
                                     kind=classify(title+" "+text))
        except (ValueError, KeyError, TypeError, AttributeError, OSError, OverflowError):
            bad += 1
    return list(good.values()), bad

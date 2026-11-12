"""Collect public two-guest hotel tariffs and keep their provenance."""

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from .sources import MSK, request_html
from .storage import latest_online_snapshot, log, save_online_snapshot

MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
RUSSIAN_DATE = r"(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?"


def plain_text(markup):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def table_rows(markup):
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", markup, re.S):
        yield [plain_text(cell) for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.S)]


def ru_date(parts, default_year=None):
    day, month, year = parts
    return date(int(year or default_year), MONTHS.index(month) + 1, int(day))


def rate_row(room, price, condition, valid_from, valid_to=None):
    compact = "".join(price.split())
    if not re.fullmatch(r"[1-9][0-9]{2,5}", compact):
        raise ValueError("Не удалось извлечь цену в рублях.")
    return dict(
        room=room,
        price_rub=int(compact),
        condition=condition,
        valid_from=str(valid_from),
        valid_to=str(valid_to) if valid_to else None,
    )


def parse_taiga(page):
    date_match = re.search(r"для периода с " + RUSSIAN_DATE, plain_text(page))
    table = re.search(r'<table class="price"[^>]*>(.*?)</table>', page, re.S)
    if not date_match or not date_match[3] or not table:
        raise ValueError("Структура прейскуранта «Таёжного» изменилась.")
    valid_from = ru_date(date_match.groups())
    header, *rows = table_rows(table[1])
    if len(header) < 3 or header[2] != "Сутки один / двое":
        raise ValueError("В прейскуранте «Таёжного» не найдена цена за двоих.")
    result = []
    for row in rows:
        if len(row) < 3 or "/" not in row[2]:
            continue
        room = re.split(r" Первая категория| Джуниор Сюит| Улучшенный люкс", row[0])[0]
        result.append(rate_row(room, row[2].split("/", 1)[1], "С завтраком, справочно", valid_from))
    return result


def parse_astrus(page):
    tab = re.search(r'<div\b[^>]*class=[\'"]prices__tab[\'"][^>]*>(.*?)</table>', page, re.S)
    if not tab:
        raise ValueError("Раздел цен «Аструса» не найден.")
    period = re.search(
        r"Цены на номера в отеле Аструс с " + RUSSIAN_DATE + r" до " + RUSSIAN_DATE,
        plain_text(tab[1]),
    )
    if not period or not period[6]:
        raise ValueError("Срок действия тарифов «Аструса» не указан.")
    valid_from = ru_date(period.groups()[:3], period[6])
    valid_to = ru_date(period.groups()[3:])
    _, guests, days, *rows = table_rows(tab[1])
    if not guests or guests[0] != "2 гостя" or days[:2] != ["Будни", "Выходные"]:
        raise ValueError("Условия размещения в прейскуранте «Аструса» изменились.")
    return [
        rate_row(row[0], price, condition, valid_from, valid_to)
        for row in rows
        if len(row) == 7 and row[0] in ("Стандарт", "Комфорт", "Студия")
        for price, condition in zip(row[1:3], ("Будни, с завтраком", "Выходные, с завтраком"))
    ]


SOURCES = (
    ("taiga_rates", "Таёжный", "https://www.tbhotel.ru/prices/", parse_taiga),
    ("astrus_rates", "Аструс", "https://astrus.bronhotel.ru/", parse_astrus),
)


def read_rate_archive(path):
    if not path.is_file():
        return {}
    return {entry["source"]: entry for entry in json.loads(path.read_text(encoding="utf-8"))}


def fetch_rates(provider, timeout):
    source, hotel, url, parse = provider
    try:
        page = request_html(url, timeout)
        rows = parse(page)
        if not rows:
            raise ValueError("Не найдено ни одного тарифа для двух гостей.")
        snapshot = dict(
            source=source,
            hotel=hotel,
            url=url,
            checked_at=datetime.now(MSK).isoformat(timespec="microseconds"),
            rates=rows,
        )
        return snapshot, page, None
    except (OSError, ValueError, LookupError) as error:
        return None, None, str(error)


def collect_market_rates(db, config, archive_path, today, online):
    archive = read_rate_archive(archive_path)
    if not online:
        return [dict(entry, status="archive") for entry in archive.values()]
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        attempts = list(
            pool.map(
                lambda provider: fetch_rates(provider, min(config["request_timeout"], 5)), SOURCES
            )
        )
    results = []
    for (source, hotel, url, _), (snapshot, page, error) in zip(SOURCES, attempts):
        parsed_source = source + "_parsed"
        with db:
            if snapshot:
                saved_at = snapshot["checked_at"]
                data = {key: value for key, value in snapshot.items() if key != "checked_at"}
                html_added = save_online_snapshot(db, source, saved_at, page)
                parsed_added = save_online_snapshot(
                    db,
                    parsed_source,
                    saved_at,
                    json.dumps(data, ensure_ascii=False, sort_keys=True),
                )
                log(db, source, today, "ok", max(html_added, parsed_added), len(data["rates"]), 0)
                results.append(dict(snapshot, status="live"))
                continue
            log(db, source, today, "failed", 0, 0, 0, error)
            previous = latest_online_snapshot(db, parsed_source)
        fallback = (
            dict(json.loads(previous[0]), checked_at=previous[1])
            if previous
            else archive.get(source)
        )
        if fallback:
            results.append(dict(fallback, status="archive"))
        else:
            results.append(
                dict(source=source, hotel=hotel, url=url, status="unavailable", rates=[])
            )
    return results

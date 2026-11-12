"""Test the project's Ridge implementation on the paper's original H1/H2 data."""

import argparse
import calendar
import csv
import hashlib
import io
import json
import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

import numpy as np

from hotel.models import Ridge, predict, scores

PAPER = "https://doi.org/10.1016/j.dib.2018.11.126"
DATA = "https://ars.els-cdn.com/content/image/1-s2.0-S2352340918315191-mmc2.zip"
MONTH = {name: number for number, name in enumerate(calendar.month_name) if number}
DAY = timedelta(days=1)
HORIZON = 14
ALPHA = 1.0
FIRST_DAY = date(2015, 8, 1)
TEST_START = date(2017, 4, 10)
WINDOWS = 8


def daily_adr(csv_file):
    """Reconstruct room-night-weighted ADR using each stay's average rate."""
    revenue, sold = Counter(), Counter()
    available_on = {}
    for booking in csv.DictReader(csv_file):
        if booking["ReservationStatus"] != "Check-Out" or booking["IsCanceled"] != "0":
            continue
        nights = int(booking["StaysInWeekNights"]) + int(booking["StaysInWeekendNights"])
        rate = float(booking["ADR"])
        if nights <= 0 or not math.isfinite(rate) or rate <= 0:
            continue
        arrival = date(
            int(booking["ArrivalDateYear"]),
            MONTH[booking["ArrivalDateMonth"]],
            int(booking["ArrivalDateDayOfMonth"]),
        )
        checkout = max(date.fromisoformat(booking["ReservationStatusDate"]), arrival + nights * DAY)
        for offset in range(nights):
            night = arrival + offset * DAY
            revenue[night] += rate
            sold[night] += 1
            available_on[night] = max(available_on.get(night, checkout), checkout)
    return {
        night: (revenue[night] / count, available_on[night])
        for night, count in sold.items()
        if night >= FIRST_DAY
    }


def design(history, origin):
    def known_values(dates):
        return [history[day][0] for day in dates if day in history and history[day][1] <= origin]

    recent = known_values(origin - offset * DAY for offset in range(84))
    if not recent:
        raise ValueError(f"Отсутствует история перед {origin}.")
    average = float(np.mean(recent[:28]))
    features = []
    for step in range(1, HORIZON + 1):
        target = origin + step * DAY
        previous = target - 7 * DAY if step <= 7 else target - 14 * DAY
        same_weekdays = known_values(previous - 7 * i * DAY for i in range(4))
        weekly_angle = math.tau * target.weekday() / 7
        annual_angle = math.tau * target.timetuple().tm_yday / 365.25
        features.append(
            [
                float(np.median(same_weekdays)) if same_weekdays else average,
                recent[0],
                average,
                math.sin(weekly_angle),
                math.cos(weekly_angle),
                math.sin(annual_angle),
                math.cos(annual_angle),
                step,
            ]
        )
    return np.asarray(features)


def evaluate(history):
    x, y, availability = [], [], []
    first_origin = FIRST_DAY + 84 * DAY
    last_day = TEST_START + (WINDOWS * HORIZON - 1) * DAY
    for step in range(0, (last_day - first_origin).days, 7):
        origin = first_origin + step * DAY
        for offset, row in enumerate(design(history, origin), 1):
            target = origin + offset * DAY
            if target in history:
                value, finalized = history[target]
                x.append(row)
                y.append(value)
                availability.append(finalized.toordinal())
    x, y, availability = np.asarray(x), np.asarray(y), np.asarray(availability)

    actual, forecast, baseline = [], [], []
    for window in range(WINDOWS):
        origin = TEST_START - DAY + window * HORIZON * DAY
        train = availability <= origin.toordinal()
        model = Ridge(ALPHA).fit(x[train], y[train])
        features = design(history, origin)
        targets = [origin + step * DAY for step in range(1, HORIZON + 1)]
        valid = [target in history for target in targets]
        actual.extend(history[target][0] for target in targets if target in history)
        forecast.extend(predict(model, features[valid], "adr"))
        baseline.extend(features[valid, 0])
    return dict(
        days=len(actual),
        ridge=scores(np.asarray(actual), np.asarray(forecast)),
        seasonal=scores(np.asarray(actual), np.asarray(baseline)),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Использовать уже скачанный mmc2.zip")
    parser.add_argument("--out", type=Path, help="Сохранить JSON-результат")
    options = parser.parse_args()
    if options.source:
        raw = options.source.read_bytes()
    else:
        with urlopen(DATA, timeout=20) as response:
            raw = response.read()
    with ZipFile(io.BytesIO(raw)) as archive:
        hotels = {}
        for code in ("H1", "H2"):
            with archive.open(f"{code}.csv") as stream:
                hotels[code] = evaluate(daily_adr(io.TextIOWrapper(stream, encoding="utf-8-sig")))
    result = dict(
        paper=PAPER,
        source=DATA,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        test_start=str(TEST_START),
        test_end=str(TEST_START + (WINDOWS * HORIZON - 1) * DAY),
        windows=WINDOWS,
        horizon=HORIZON,
        alpha=ALPHA,
        hotels=hotels,
    )
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if options.out:
        options.out.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()

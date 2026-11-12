"""Try the online event providers in order and record each attempt."""

import json
from dataclasses import dataclass
from datetime import datetime

from .sources import (
    DAY,
    MSK,
    fetch_events,
    fetch_mos_afisha,
    parse_mos_afisha,
    parse_public,
)
from .storage import log, save_online_snapshot

SOURCE_NAMES = {"kudago_json": "KudaGo", "mos_afisha": "Мосбилет"}


@dataclass(frozen=True)
class LiveSchedule:
    source: str
    checked_at: str
    events: list


def load_live_schedule(db, config, today):
    first_day = today + DAY
    last_day = today + config["horizon"] * DAY
    # Bound each provider's wait so an unavailable API cannot hold up a demo.
    request_config = {
        **config,
        "request_retries": 1,
        "request_timeout": min(config["request_timeout"], 5),
    }
    providers = (
        ("kudago_json", fetch_events, parse_public),
        ("mos_afisha", fetch_mos_afisha, parse_mos_afisha),
    )
    for source, fetch, parse in providers:
        try:
            payload = fetch(request_config, first_day, last_day)
            events, rejected = parse(payload, today, first_day, last_day)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            with db:
                log(db, source, today, "failed", 0, 0, 0, str(error))
            continue
        if not events:
            with db:
                log(
                    db,
                    source,
                    today,
                    "empty",
                    0,
                    0,
                    rejected,
                    "В ответе нет подходящих событий на горизонте прогноза.",
                )
            continue
        checked_at = datetime.now(MSK).isoformat(timespec="microseconds")
        with db:
            added = save_online_snapshot(
                db, source, checked_at, json.dumps(payload, ensure_ascii=False, sort_keys=True)
            )
            log(db, source, today, "ok", added, len(events), rejected)
        return LiveSchedule(source, checked_at, events)
    return None

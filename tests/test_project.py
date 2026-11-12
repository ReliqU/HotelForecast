import json
import unittest
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from hotel.archive import ARCHIVE_SOURCE, parse_archive
from hotel.audit import audit_datasets, promote
from hotel.cli import ROOT, render
from hotel.features import FeatureStore
from hotel.live import load_live_schedule
from hotel.models import Hybrid, Ridge, Robust, predict
from hotel.pipeline import replay, run, split
from hotel.rates import collect_market_rates, parse_astrus, parse_taiga
from hotel.sources import DAY, html_feed, parse_html, parse_public, room_feed
from hotel.storage import (
    connect,
    fingerprint,
    ingest,
    ingest_archive,
    normalize_rooms,
)
from hotel.stress import scenario_sets

CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
ANCHOR = date.fromisoformat(CFG["as_of"])
ARCHIVE = ROOT / "data" / "public_events.json"


class Inputs(unittest.TestCase):
    def test_quality_flags_and_missing_revenue(self):
        payload = "hotel,day,available,sold,revenue\ncenter,2026-11-01,100,60,\nexpo,2026-11-01,120,130,400\n"
        rows, flags = normalize_rooms(payload, date(2026, 11, 1), {"center": 120, "expo": 156})
        self.assertEqual(flags, 2)
        self.assertEqual(rows[0][-2:], (60, None))
        self.assertEqual(rows[1][-2:], (None, None))

    def test_html_parser_extracts_category_and_venue(self):
        html = (
            '<article id="1"><h2>Форум &amp; выставка</h2><time datetime="2026-11-14"></time>'
            '<address data-lat="55.75" data-lon="37.55">Павильон</address></article>'
        )
        rows, flags = parse_html(html, ANCHOR)
        self.assertEqual(flags, 0)
        self.assertEqual(
            (rows[0]["kind"], rows[0]["venue"], rows[0]["lat"]), ("expo", "Павильон", 55.75)
        )
        self.assertEqual(parse_html('<article id="2"></article>', ANCHOR), ([], 1))

    def test_public_event_available_only_after_local_snapshot(self):
        from datetime import datetime

        from hotel.sources import MSK

        def ts(day):
            return int(datetime(day.year, day.month, day.day, 12, tzinfo=MSK).timestamp())

        items = [
            {
                "id": 1,
                "title": "Концерт",
                "publication_date": ts(ANCHOR - 30 * DAY),
                "dates": [{"start": ts(ANCHOR + 2 * DAY)}],
                "place": None,
            }
        ]
        rows, bad = parse_public(items, ANCHOR, ANCHOR + DAY, ANCHOR + 14 * DAY)
        self.assertEqual(bad, 0)
        self.assertEqual(rows[0]["published"], ANCHOR)
        self.assertEqual(rows[0]["kind"], "music")
        self.assertIsNone(rows[0]["lat"])

    def test_sources_are_reproducible(self):
        self.assertEqual(room_feed(CFG, ANCHOR), room_feed(CFG, ANCHOR))
        self.assertEqual(html_feed(CFG, ANCHOR), html_feed(CFG, ANCHOR))

    def test_archive_rejects_unconfirmed_availability(self):
        obj = json.loads(ARCHIVE.read_text(encoding="utf-8"))
        row = next(r for r in obj["events"] if r["availability_basis"] == "observed")
        row["available_from"] = str(ANCHOR - DAY)
        with self.assertRaisesRegex(ValueError, "дата доступности"):
            parse_archive(json.dumps(obj))

    def test_temporal_split_has_no_label_overlap_across_periods(self):
        (train, val, test, refit), (fit_end, val_end, _) = split(CFG, ANCHOR)
        self.assertLessEqual(train[-1] + 14 * DAY, fit_end)
        self.assertLessEqual(val[-1] + 14 * DAY, val_end)
        self.assertLessEqual(refit[-1] + 14 * DAY, val_end)
        self.assertGreater(val[0] + DAY, fit_end)
        self.assertGreater(test[0] + DAY, val_end)

    def test_huber_limits_single_outlier_influence(self):
        x = np.linspace(-1.0, 1.0, 81)[:, None]
        clean = 20 + 4 * x[:, 0] + 0.08 * np.sin(np.arange(len(x)))
        damaged = clean.copy()
        damaged[-1] += 100
        ridge = Ridge(0.1).fit(x, damaged)
        huber = Robust(0.1, 1.2, False, None).fit(x, damaged)
        self.assertLess(
            np.mean(abs(predict(huber, x, "adr") - clean)),
            np.mean(abs(predict(ridge, x, "adr") - clean)) * 0.3,
        )

    def test_hybrid_selects_component_at_exact_cutoff(self):
        x = np.array([[i, float(i % 4), float(i % 5)] for i in range(1, 15)], dtype=float)
        short = Robust(1.0, 1.2, True, None).fit(x, 30 + 0.2 * x[:, 0] + x[:, 1])
        long = Ridge(1.0).fit(x, 50 + 0.5 * x[:, 0] + x[:, 2])
        m = Hybrid(short, long)
        joined = np.r_[predict(short, x[:7], "adr"), predict(long, x[7:], "adr")]
        np.testing.assert_allclose(predict(m, x, "adr"), joined, rtol=0, atol=1e-10)
        self.assertEqual(m.contrib(x).shape, x.shape)

    def test_promotion_requires_mae_gain_and_rmse_guard(self):
        def comparison(mae, rmse):
            return dict(previous=dict(mae=4.0, rmse=5.0), candidate=dict(mae=mae, rmse=rmse))

        self.assertTrue(promote(comparison(3.9, 5.1)))
        self.assertFalse(promote(comparison(4.0, 4.8)))
        self.assertFalse(promote(comparison(3.9, 5.26)))


class PublicRates(unittest.TestCase):
    def test_public_tariffs_extract_two_guest_prices(self):
        taiga = """
            <p>Цены указаны справочно, для периода с 1 января 2026г.</p>
            <table class="price">
              <tr><th>Номер</th><th>За час</th><th>Сутки<span>один / двое</span></th></tr>
              <tr><th>Стандарт</th><td>1700</td><td>4&nbsp;500 / 5&nbsp;900</td></tr>
            </table>"""
        astrus = """
            <div class='prices__tab'>
              <p>Цены на номера в отеле Аструс с 12 января до 30 декабря 2026</p>
              <table>
                <tr><th>Категория номера</th></tr>
                <tr><th>2 гостя</th><th>1 гость</th></tr>
                <tr><th>Будни</th><th>Выходные</th></tr>
                <tr><td>Стандарт</td><td>9000</td><td>10100</td><td>8400</td><td>8590</td>
                    <td>2500</td><td>2300</td></tr>
              </table>
            </div>"""
        self.assertEqual(parse_taiga(taiga)[0]["price_rub"], 5900)
        self.assertEqual([row["price_rub"] for row in parse_astrus(astrus)], [9000, 10100])
        self.assertEqual(parse_astrus(astrus)[0]["valid_to"], "2026-12-30")

    def test_rate_archive_keeps_the_original_timestamp_on_failure(self):
        path = ROOT / "data" / "public_rates.json"
        with (
            closing(connect(":memory:", CFG)) as db,
            patch("hotel.rates.request_html", side_effect=TimeoutError("test timeout")),
        ):
            before = fingerprint(db, ANCHOR)
            snapshots = collect_market_rates(db, CFG, path, ANCHOR, online=True)
            self.assertEqual(snapshots, collect_market_rates(db, CFG, path, ANCHOR, online=False))
            self.assertEqual(len(snapshots), 2)
            self.assertEqual({item["status"] for item in snapshots}, {"archive"})
            self.assertEqual(fingerprint(db, ANCHOR), before)

    def test_repeat_rates_reuse_storage_and_fallback_to_the_last_seen_prices(self):
        path = ROOT / "data" / "public_rates.json"
        sources = {item["source"]: item for item in json.loads(path.read_text(encoding="utf-8"))}

        def fetch(provider, timeout):
            source = sources[provider[0]]
            rates = [dict(row, price_rub=row["price_rub"] + delta) for row in source["rates"]]
            return dict(source, rates=rates, checked_at=stamp), f"<table>{delta}</table>", None

        first_stamp = "2026-11-12T10:00:00.000000+03:00"
        last_stamp = "2026-11-12T10:01:00.000000+03:00"
        with closing(connect(":memory:", CFG)) as db:
            with patch("hotel.rates.fetch_rates", side_effect=fetch):
                for delta, stamp in (
                    (0, first_stamp),
                    (0, first_stamp),
                    (100, first_stamp),
                    (0, last_stamp),
                ):
                    latest = collect_market_rates(db, CFG, path, ANCHOR, online=True)
            raw_counts = db.execute("SELECT count(*) FROM raw GROUP BY source").fetchall()
            self.assertEqual([row[0] for row in raw_counts], [2, 2, 2, 2])
            added = db.execute("SELECT added_slices FROM loads ORDER BY id").fetchall()
            self.assertEqual([row[0] for row in added], [1, 1, 0, 0, 1, 1, 0, 0])
            with patch("hotel.rates.request_html", side_effect=TimeoutError("test timeout")):
                fallback = collect_market_rates(db, CFG, path, ANCHOR, online=True)
            self.assertEqual(fallback, [dict(snapshot, status="archive") for snapshot in latest])
            self.assertTrue(all(snapshot["checked_at"] == last_stamp for snapshot in fallback))


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.db = connect(Path(cls.tmp.name) / "test.sqlite", CFG)
        cls.addClassCleanup(cls.db.close)
        cls.initial = ingest(cls.db, CFG, ANCHOR)
        if not ingest_archive(cls.db, ARCHIVE, ANCHOR):
            raise AssertionError("bundled archive failed to load")
        cls.data = run(cls.db, CFG, ANCHOR)

    def test_ingestion_is_incremental(self):
        with TemporaryDirectory() as tmp, closing(connect(Path(tmp) / "partial.sqlite", CFG)) as db:
            start = date.fromisoformat(CFG["start"])
            first = ingest(db, CFG, start)
            digest = fingerprint(db, start)
            second = ingest(db, CFG, start)
            third = ingest(db, CFG, start + DAY)
            self.assertEqual([r["added_slices"] for r in first], [1, 35])
            self.assertEqual([r["added_slices"] for r in second], [0, 0])
            self.assertEqual([r["added_slices"] for r in third], [1, 1])
            self.assertEqual(fingerprint(db, start), digest)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], 6)

    def test_ingestion_transaction_rolls_back_on_source_failure(self):
        with (
            TemporaryDirectory() as tmp,
            closing(connect(Path(tmp) / "rollback.sqlite", CFG)) as db,
        ):
            start = date.fromisoformat(CFG["start"])
            with patch(
                "hotel.storage.room_feed",
                side_effect=[room_feed(CFG, start), OSError("demo outage")],
            ):
                with self.assertRaises(OSError):
                    ingest(db, CFG, start + DAY)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT status FROM loads").fetchone()[0], "failed")
            self.assertEqual(ingest(db, CFG, start + DAY)[0]["rows"], 6)

    def test_public_outage_does_not_change_historical_data(self):
        next_day = ANCHOR + DAY
        item = dict(
            id=100,
            dateFrom=f"{next_day} 19:00:00",
            dateTo=f"{next_day + 5 * DAY} 22:00:00",
            title="Концерт",
            venueLat="55.75",
            venueLon="37.61",
            url="/event/100",
        )
        with closing(connect(":memory:", CFG)) as db:
            before = fingerprint(db, ANCHOR)
            with (
                patch("hotel.live.fetch_events", side_effect=TimeoutError("test timeout")),
                patch("hotel.live.fetch_mos_afisha", return_value=[item]),
            ):
                schedule = load_live_schedule(db, CFG, ANCHOR)
            self.assertEqual(schedule.source, "mos_afisha")
            self.assertEqual([event["date"] for event in schedule.events], [next_day])
            self.assertEqual(schedule.events[0]["published"], ANCHOR)
            self.assertEqual(schedule.events[0]["url"], "https://bilet.mos.ru/event/100")
            with (
                patch("hotel.live.fetch_events", return_value=[]),
                patch("hotel.live.fetch_mos_afisha", side_effect=TimeoutError("test timeout")),
            ):
                self.assertIsNone(load_live_schedule(db, CFG, ANCHOR))
            self.assertEqual(
                [row[0] for row in db.execute("SELECT status FROM loads ORDER BY id")],
                ["failed", "ok", "empty", "failed"],
            )
            self.assertEqual(fingerprint(db, ANCHOR), before)

    def test_online_events_deduplicate_even_at_the_same_timestamp(self):
        item = dict(
            id=101,
            dateFrom=f"{ANCHOR + DAY} 19:00:00",
            title="Концерт",
        )
        changed = dict(item, title="Новый концерт")
        with (
            closing(connect(":memory:", CFG)) as db,
            patch("hotel.live.fetch_events", side_effect=TimeoutError("test timeout")),
            patch("hotel.live.fetch_mos_afisha", side_effect=([item], [item], [changed])),
            patch("hotel.live.datetime") as clock,
        ):
            clock.now.return_value = datetime.fromisoformat("2026-11-12T10:00:00+03:00")
            for expected_title in (item["title"], item["title"], changed["title"]):
                schedule = load_live_schedule(db, CFG, ANCHOR)
                self.assertEqual(schedule.events[0]["title"], expected_title)
            added = db.execute(
                "SELECT added_slices FROM loads WHERE source='mos_afisha' ORDER BY id"
            ).fetchall()
            self.assertEqual([row[0] for row in added], [1, 0, 1])
            raw = db.execute("SELECT count(*) FROM raw WHERE source='mos_afisha'").fetchone()
            self.assertEqual(raw[0], 2)

    def test_future_rows_cannot_change_features_at_origin(self):
        origin = ANCHOR - 20 * DAY
        d = origin + 8 * DAY
        fs = FeatureStore(self.db, CFG, ANCHOR)
        x, _ = fs.row("center", origin, d)
        try:
            self.db.execute(
                "UPDATE rooms SET sold=1,revenue=1 WHERE day>? AND hotel='center'", (str(origin),)
            )
            self.db.execute(
                "INSERT INTO events VALUES('event_html','test-future',?,?,?,?,?,?,?)",
                (str(d), str(origin + DAY), "Future", "Venue", "music", 55.75, 37.6),
            )
            other = FeatureStore(self.db, CFG, ANCHOR)
            np.testing.assert_array_equal(other.row("center", origin, d)[0], x)
        finally:
            self.db.rollback()

    def test_seasonal_median_skips_missing_and_ignores_future(self):
        with closing(connect(":memory:", CFG)) as db:
            # Four same weekdays include a missing row; a wrong weekday and a
            # future row have extreme values but cannot change the median.
            for offset, sold in ((0, 20), (7, 90), (14, None), (21, 50), (1, 99), (-7, 100)):
                db.execute(
                    "INSERT INTO rooms VALUES (?,?,?,?,?)",
                    ("center", str(ANCHOR - offset * DAY), 100, sold, None),
                )
            fs = FeatureStore(db, CFG, ANCHOR + 7 * DAY)
            self.assertEqual(fs.seasonal("center", ANCHOR, ANCHOR + 7 * DAY, 0), (20.0, 50.0))

    def test_archive_idempotence_and_provenance_gate(self):
        before = fingerprint(self.db, ANCHOR)
        self.assertTrue(ingest_archive(self.db, ARCHIVE, ANCHOR))
        self.assertEqual(
            self.db.execute("SELECT added_slices FROM loads ORDER BY id DESC LIMIT 1").fetchone()[
                0
            ],
            0,
        )
        self.assertEqual(fingerprint(self.db, ANCHOR), before)
        fs = FeatureStore(self.db, CFG, ANCHOR)
        day = date(2026, 11, 17)
        old = fs.available_events(ANCHOR - 2 * DAY, day, ARCHIVE_SOURCE)
        after_publication = fs.available_events(ANCHOR - DAY, day, ARCHIVE_SOURCE)
        current = fs.available_events(ANCHOR, day, ARCHIVE_SOURCE)
        self.assertEqual(len(old), 0)
        self.assertEqual([e["title"] for e in after_publication], ["CleanExpo Moscow 2026"])
        self.assertEqual({e["basis"] for e in current}, {"published", "observed"})
        self.assertTrue(all(e["url"].startswith("https://") for e in current))
        self.assertEqual(len(self.data["archive"]["events"]), 5)
        ix = (day - ANCHOR).days - 1
        public = self.data["forecasts"][ARCHIVE_SOURCE]["center"][ix]
        synthetic = self.data["forecasts"]["event_html"]["center"][ix]
        self.assertEqual(len(public["events"]), 2)
        self.assertNotAlmostEqual(public["occupancy"]["value"], synthetic["occupancy"]["value"])
        self.assertEqual(public["occupancy"]["baseline"], synthetic["occupancy"]["baseline"])

    def test_stress_scenarios_are_paired_and_isolated(self):
        before = self.db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
        sets, meta = scenario_sets(self.db, CFG, ANCHOR)
        x, y, _, rows = sets["control"][0]
        late_x, late_y, _, late_rows = sets["late"][0]
        weak_x, weak_y, _, weak_rows = sets["weak"][0]
        self.assertEqual(rows, late_rows)
        self.assertEqual(rows, weak_rows)
        self.assertEqual(meta["start"], str(ANCHOR + DAY))
        self.assertTrue(all(d > ANCHOR and o >= ANCHOR for o, _, d in rows))
        np.testing.assert_array_equal(y, late_y)
        np.testing.assert_array_equal(x[:, :-5], late_x[:, :-5])
        np.testing.assert_array_equal(x[:, -5:], weak_x[:, -5:])
        self.assertTrue(np.any(x[:, -5:] != late_x[:, -5:]))
        self.assertFalse(np.array_equal(y, weak_y, equal_nan=True))
        self.assertGreater(sets["late"][1]["shifted"], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], before)

    def test_explanations_reconcile_and_forecasts_are_valid(self):
        for rows in self.data["forecast"].values():
            self.assertEqual(len(rows), 14)
            self.assertEqual(rows[-1]["date"], str(ANCHOR + 14 * DAY))
            for row in rows:
                self.assertTrue(0 <= row["occupancy"]["value"] <= 100)
                self.assertGreater(row["adr"]["value"], 0)
                for target in ("occupancy", "adr"):
                    p = row[target]
                    self.assertAlmostEqual(
                        p["intercept"] + sum(p["contributions"]) + p["clip"], p["value"], places=8
                    )

    def test_model_selection_and_replay(self):
        for key, t in self.data["targets"].items():
            self.assertEqual(
                {r["family"] for r in t["finalists"]},
                {"seasonal", "median", "ridge", "boost", "robust", "hybrid"},
            )
            self.assertEqual(t["baseline"]["family"], "median")
            self.assertEqual(
                t["validation_winner"]["id"],
                min(t["candidates"], key=lambda r: r["validation"]["mae"])["id"],
            )
            confirm = self.data["confirmation"]["targets"][key]
            self.assertEqual(t["winner"]["family"], "hybrid" if promote(confirm) else "ridge")
            self.assertTrue(replay(self.db, t["winner"]["id"])["reproduced"])
            self.assertGreater(t["samples"]["test"], 300)

    def test_confirmation_starts_after_audit_and_keeps_facts_isolated(self):
        before = self.db.execute("SELECT MAX(day) FROM rooms").fetchone()[0]
        (explore, _, a), (confirm, _, b) = audit_datasets(self.db, CFG, ANCHOR)
        self.assertEqual(b["start"], str(date.fromisoformat(a["end"]) + DAY))
        self.assertEqual(a["start"], str(date.fromisoformat(self.data["stress"]["end"]) + DAY))
        self.assertTrue(all(d > date.fromisoformat(a["end"]) for _, _, d in confirm[3]))
        self.assertLess(max(d for _, _, d in explore[3]), min(d for _, _, d in confirm[3]))
        self.assertEqual(self.db.execute("SELECT MAX(day) FROM rooms").fetchone()[0], before)
        for r in self.data["confirmation"]["targets"].values():
            self.assertEqual(
                r["by_horizon"]["8–14"]["previous"], r["by_horizon"]["8–14"]["candidate"]
            )

    def test_portable_dashboard_embeds_results(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp)
            render(p, self.data)
            html = (p / "forecast.html").read_text(encoding="utf-8")
            payload = html.split('id="payload">')[1].split("</script>")[0]
            self.assertEqual(json.loads(payload)["data_hash"], self.data["data_hash"])
            self.assertNotIn("__HOTEL_", html)
            self.assertIn("function renderDashboard()", html)
            self.assertIn(".workspace {", html)


if __name__ == "__main__":
    unittest.main()

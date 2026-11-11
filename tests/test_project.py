from contextlib import closing
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from hotel.archive import ARCHIVE_SOURCE, parse_archive
from hotel.cli import ROOT, render
from hotel.features import FeatureStore
from hotel.pipeline import replay, run, split
from hotel.sources import DAY, html_feed, parse_html, parse_public, room_feed
from hotel.storage import connect, fingerprint, ingest, ingest_archive, ingest_public, normalize_rooms
from hotel.stress import scenario_sets

CFG = json.loads((ROOT/"config.json").read_text(encoding="utf-8"))
ANCHOR = date.fromisoformat(CFG["as_of"])
ARCHIVE = ROOT/"data"/"public_events.json"


class Inputs(unittest.TestCase):
    def test_quality_flags_and_missing_revenue(self):
        payload = "hotel,day,available,sold,revenue\ncenter,2026-11-01,100,60,\nexpo,2026-11-01,120,130,400\n"
        rows, flags = normalize_rooms(payload, date(2026, 11, 1), {"center": 120, "expo": 156})
        self.assertEqual(flags, 2)
        self.assertEqual(rows[0][-2:], (60, None))
        self.assertEqual(rows[1][-2:], (None, None))

    def test_html_parser_extracts_category_and_venue(self):
        html = '<article id="1"><h2>Форум &amp; выставка</h2><time datetime="2026-11-14"></time>' \
               '<address data-lat="55.75" data-lon="37.55">Павильон</address></article>'
        rows, flags = parse_html(html, ANCHOR)
        self.assertEqual(flags, 0)
        self.assertEqual((rows[0]["kind"], rows[0]["venue"], rows[0]["lat"]), ("expo", "Павильон", 55.75))
        self.assertEqual(parse_html('<article id="2"></article>', ANCHOR), ([], 1))

    def test_public_event_available_only_after_local_snapshot(self):
        from datetime import datetime
        from hotel.sources import MSK
        ts = lambda d: int(datetime(d.year, d.month, d.day, 12, tzinfo=MSK).timestamp())
        items = [{"id": 1, "title": "Концерт", "publication_date": ts(ANCHOR-30*DAY),
                  "dates": [{"start": ts(ANCHOR+2*DAY)}], "place": None}]
        rows, bad = parse_public(items, ANCHOR, ANCHOR+DAY, ANCHOR+14*DAY)
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
        row["available_from"] = str(ANCHOR-DAY)
        with self.assertRaisesRegex(ValueError,"дата доступности"):
            parse_archive(json.dumps(obj))

    def test_temporal_split_has_no_label_overlap_across_periods(self):
        (train, val, test, refit), (fit_end, val_end, _) = split(CFG, ANCHOR)
        self.assertLessEqual(train[-1]+14*DAY, fit_end)
        self.assertLessEqual(val[-1]+14*DAY, val_end)
        self.assertLessEqual(refit[-1]+14*DAY, val_end)
        self.assertGreater(val[0]+DAY, fit_end)
        self.assertGreater(test[0]+DAY, val_end)


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.db = connect(Path(cls.tmp.name)/"test.sqlite", CFG)
        cls.addClassCleanup(cls.db.close)
        cls.initial = ingest(cls.db, CFG, ANCHOR)
        if not ingest_archive(cls.db,ARCHIVE,ANCHOR):
            raise AssertionError("bundled archive failed to load")
        cls.data = run(cls.db, CFG, ANCHOR)

    def test_ingestion_is_incremental(self):
        with TemporaryDirectory() as tmp, closing(connect(Path(tmp)/"partial.sqlite", CFG)) as db:
            start = date.fromisoformat(CFG["start"])
            first = ingest(db, CFG, start)
            digest = fingerprint(db, start)
            second = ingest(db, CFG, start)
            third = ingest(db, CFG, start+DAY)
            self.assertEqual([r["added_slices"] for r in first], [1, 35])
            self.assertEqual([r["added_slices"] for r in second], [0, 0])
            self.assertEqual([r["added_slices"] for r in third], [1, 1])
            self.assertEqual(fingerprint(db, start), digest)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], 6)

    def test_ingestion_transaction_rolls_back_on_source_failure(self):
        with TemporaryDirectory() as tmp, closing(connect(Path(tmp)/"rollback.sqlite", CFG)) as db:
            start = date.fromisoformat(CFG["start"])
            with patch("hotel.storage.room_feed", side_effect=[room_feed(CFG, start), OSError("demo outage")]):
                with self.assertRaises(OSError):
                    ingest(db, CFG, start+DAY)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT status FROM loads").fetchone()[0], "failed")
            self.assertEqual(ingest(db, CFG, start+DAY)[0]["rows"], 6)

    def test_public_outage_does_not_change_historical_data(self):
        before = fingerprint(self.db, ANCHOR)
        with patch("hotel.storage.fetch_events", side_effect=TimeoutError("test timeout")):
            self.assertFalse(ingest_public(self.db, CFG, ANCHOR))
        self.assertEqual(fingerprint(self.db, ANCHOR), before)
        with patch("hotel.storage.fetch_events", return_value=[]):
            self.assertTrue(ingest_public(self.db, CFG, ANCHOR))
        with patch("hotel.storage.fetch_events", side_effect=AssertionError("should use cache")):
            self.assertTrue(ingest_public(self.db, CFG, ANCHOR))

    def test_future_rows_cannot_change_features_at_origin(self):
        origin = ANCHOR-20*DAY
        d = origin+8*DAY
        fs = FeatureStore(self.db, CFG, ANCHOR)
        x, _ = fs.row("center", origin, d)
        try:
            self.db.execute("UPDATE rooms SET sold=1,revenue=1 WHERE day>? AND hotel='center'", (str(origin),))
            self.db.execute("INSERT INTO events VALUES('event_html','test-future',?,?,?,?,?,?,?)",
                            (str(d), str(origin+DAY), "Future", "Venue", "music", 55.75, 37.6))
            other = FeatureStore(self.db, CFG, ANCHOR)
            np.testing.assert_array_equal(other.row("center", origin, d)[0], x)
        finally:
            self.db.rollback()

    def test_seasonal_median_skips_missing_and_ignores_future(self):
        with closing(connect(":memory:",CFG)) as db:
            # Four same weekdays include a missing row; a wrong weekday and a
            # future row have extreme values but cannot change the median.
            for offset, sold in ((0,20),(7,90),(14,None),(21,50),(1,99),(-7,100)):
                db.execute("INSERT INTO rooms VALUES (?,?,?,?,?)",
                           ("center",str(ANCHOR-offset*DAY),100,sold,None))
            fs=FeatureStore(db,CFG,ANCHOR+7*DAY)
            self.assertEqual(fs.seasonal("center",ANCHOR,ANCHOR+7*DAY,0),(20.,50.))

    def test_archive_idempotence_and_provenance_gate(self):
        before=fingerprint(self.db,ANCHOR)
        self.assertTrue(ingest_archive(self.db,ARCHIVE,ANCHOR))
        self.assertEqual(self.db.execute("SELECT added_slices FROM loads ORDER BY id DESC LIMIT 1").fetchone()[0],0)
        self.assertEqual(fingerprint(self.db,ANCHOR),before)
        fs=FeatureStore(self.db,CFG,ANCHOR)
        day=date(2026,11,17)
        old=fs.available_events(ANCHOR-2*DAY,day,ARCHIVE_SOURCE)
        after_publication=fs.available_events(ANCHOR-DAY,day,ARCHIVE_SOURCE)
        current=fs.available_events(ANCHOR,day,ARCHIVE_SOURCE)
        self.assertEqual(len(old),0)
        self.assertEqual([e["title"] for e in after_publication],["CleanExpo Moscow 2026"])
        self.assertEqual({e["basis"] for e in current},{"published","observed"})
        self.assertTrue(all(e["url"].startswith("https://") for e in current))
        self.assertEqual(len(self.data["archive"]["events"]),5)
        ix=(day-ANCHOR).days-1
        public=self.data["forecasts"][ARCHIVE_SOURCE]["center"][ix]
        synthetic=self.data["forecasts"]["event_html"]["center"][ix]
        self.assertEqual(len(public["events"]),2)
        self.assertNotAlmostEqual(public["occupancy"]["value"],synthetic["occupancy"]["value"])
        self.assertEqual(public["occupancy"]["baseline"],synthetic["occupancy"]["baseline"])

    def test_stress_scenarios_are_paired_and_isolated(self):
        before=self.db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
        sets, meta=scenario_sets(self.db,CFG,ANCHOR)
        x,y,_,rows=sets["control"][0]
        late_x,late_y,_,late_rows=sets["late"][0]
        weak_x,weak_y,_,weak_rows=sets["weak"][0]
        self.assertEqual(rows,late_rows)
        self.assertEqual(rows,weak_rows)
        self.assertEqual(meta["start"],str(ANCHOR+DAY))
        self.assertTrue(all(d>ANCHOR and o>=ANCHOR for o,_,d in rows))
        np.testing.assert_array_equal(y,late_y)
        np.testing.assert_array_equal(x[:,:-5],late_x[:,:-5])
        np.testing.assert_array_equal(x[:,-5:],weak_x[:,-5:])
        self.assertTrue(np.any(x[:,-5:]!=late_x[:,-5:]))
        self.assertFalse(np.array_equal(y,weak_y,equal_nan=True))
        self.assertGreater(sets["late"][1]["shifted"],0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],before)

    def test_explanations_reconcile_and_forecasts_are_valid(self):
        for rows in self.data["forecast"].values():
            self.assertEqual(len(rows), 14)
            self.assertEqual(rows[-1]["date"], str(ANCHOR+14*DAY))
            for row in rows:
                self.assertTrue(0 <= row["occupancy"]["value"] <= 100)
                self.assertGreater(row["adr"]["value"], 0)
                for target in ("occupancy", "adr"):
                    p = row[target]
                    self.assertAlmostEqual(p["intercept"]+sum(p["contributions"])+p["clip"], p["value"], places=8)

    def test_model_selection_and_replay(self):
        for t in self.data["targets"].values():
            self.assertEqual({r["family"] for r in t["finalists"]}, {"seasonal","median","ridge","boost"})
            self.assertEqual(t["baseline"]["family"],"median")
            self.assertEqual(t["winner"]["id"], min(t["candidates"], key=lambda r: r["validation"]["mae"])["id"])
            self.assertTrue(replay(self.db, t["winner"]["id"])["reproduced"])
            self.assertGreater(t["samples"]["test"], 300)

    def test_portable_dashboard_embeds_results(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp)
            render(p, self.data)
            html = (p/"forecast.html").read_text(encoding="utf-8")
            payload = html.split('id="payload">')[1].split("</script>")[0]
            self.assertEqual(json.loads(payload)["data_hash"], self.data["data_hash"])
            self.assertNotIn("__HOTEL_DATA__", html)


if __name__ == "__main__":
    unittest.main()

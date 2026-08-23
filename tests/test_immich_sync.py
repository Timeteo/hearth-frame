import datetime as dt
import importlib.util
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "immich_sync", Path(__file__).parents[1] / "tools" / "immich-sync.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def asset(number, day):
    return {
        "id": f"00000000-0000-4000-8000-{number:012d}",
        "type": "IMAGE",
        "localDateTime": day.isoformat() + "T12:00:00.000Z",
        "exifInfo": {"exifImageWidth": 4000, "exifImageHeight": 3000},
    }


class SelectionTests(unittest.TestCase):
    def test_anniversary_wraps_year_boundary(self):
        self.assertEqual(2, MODULE.anniversary_delta(dt.date(2019, 12, 31), dt.date(2026, 1, 2)))

    def test_bounded_disjoint_pools(self):
        today = dt.date(2026, 8, 23)
        assets = []
        for i in range(100):
            assets.append(asset(i, today - dt.timedelta(days=i % 60)))
        for i in range(100, 300):
            year = 2010 + i % 15
            assets.append(asset(i, dt.date(year, 8, 1 + i % 28)))
        selected = MODULE.select_assets(assets, "album", today, 120, .25, .30, 90, 14)
        ids = [item[0]["id"] for item in selected]
        self.assertEqual(120, len(selected))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(30, sum(pool == "recent" for _, pool in selected))

    def test_selection_is_stable_within_week(self):
        today = dt.date(2026, 8, 23)
        assets = [asset(i, dt.date(2010 + i % 10, 1 + i % 12, 1 + i % 27)) for i in range(300)]
        first = MODULE.select_assets(assets, "album", today, 100, .25, .30, 90, 14)
        second = MODULE.select_assets(assets, "album", today, 100, .25, .30, 90, 14)
        self.assertEqual(first, second)

    def test_blocked_asset_is_excluded(self):
        today = dt.date(2026, 8, 23)
        assets = [asset(i, dt.date(2020, 1, 1)) for i in range(10)]
        blocked = {assets[0]["id"] + ".jpg"}
        selected = MODULE.select_assets(assets, "album", today, 10, 0, 0, 90, 14, blocked)
        self.assertNotIn(assets[0]["id"], {item[0]["id"] for item in selected})


if __name__ == "__main__":
    unittest.main()

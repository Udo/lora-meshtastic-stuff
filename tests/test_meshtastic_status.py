import contextlib
import io
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import meshtastic_status as status


class FakeMyInfo:
    my_node_num = 123


class FakeInterface:
    def __init__(self) -> None:
        self.myInfo = FakeMyInfo()
        self.nodes = {
            "self": {"num": 123, "user": {"id": "!self", "shortName": "SELF"}},
            "good-direct": {
                "num": 456,
                "snr": 6.0,
                "hopsAway": 0,
                "user": {"id": "!good", "shortName": "GOOD"},
            },
            "good-multihop": {
                "num": 789,
                "snr": "4.5",
                "hopsAway": 2,
                "user": {"id": "!multi", "shortName": "MULTI"},
            },
            "missing-snr": {
                "num": 999,
                "hopsAway": 1,
                "user": {"id": "!nosnr", "shortName": "NOSNR"},
            },
            "bad-snr": {
                "num": 1000,
                "snr": {"broken": True},
                "hopsAway": 3,
                "user": {"id": "!bad", "shortName": "BAD"},
            },
        }


class MeshtasticStatusNeighborsTests(unittest.TestCase):
    def test_collect_neighbor_rows_skips_invalid_snr_records(self) -> None:
        rows = status.collect_neighbor_rows(FakeInterface())

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "!good")
        self.assertEqual(rows[1]["id"], "!multi")

    def test_render_neighbors_reports_summary_without_crashing(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            status.render_neighbors(FakeInterface())

        rendered = output.getvalue()
        self.assertIn("Neighbor Signals", rendered)
        self.assertIn("Neighbors with SNR", rendered)
        self.assertIn("Direct neighbors", rendered)
        self.assertIn("!good", rendered)
        self.assertIn("!multi", rendered)


if __name__ == "__main__":
    unittest.main()

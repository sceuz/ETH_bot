import sys
from types import SimpleNamespace
import unittest

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    sys.modules["requests"] = SimpleNamespace(Session=object, RequestException=Exception)

from main import PricePoint, RollingRelativeMove


class RollingRelativeMoveTests(unittest.TestCase):
    def test_returns_none_during_warmup(self) -> None:
        monitor = RollingRelativeMove(window_seconds=60, min_samples=3)
        monitor.add(PricePoint(0.0, 100.0, 100.0))
        monitor.add(PricePoint(10.0, 101.0, 101.0))
        self.assertIsNone(monitor.calculate())

    def test_removes_market_component(self) -> None:
        monitor = RollingRelativeMove(window_seconds=300, min_samples=5)
        for index, btc_price in enumerate((100.0, 101.0, 99.0, 102.0, 103.0)):
            monitor.add(PricePoint(index * 10.0, btc_price**2 / 100.0, btc_price))

        result = monitor.calculate()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result.beta, 2.0, places=10)
        self.assertAlmostEqual(result.residual_change_percent, 0.0, places=10)

    def test_reports_independent_eth_move_when_btc_is_flat(self) -> None:
        monitor = RollingRelativeMove(window_seconds=300, min_samples=4)
        for index, eth_price in enumerate((100.0, 101.0, 102.0, 105.0)):
            monitor.add(PricePoint(index * 10.0, eth_price, 200.0))

        result = monitor.calculate()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.beta, 0.0)
        self.assertAlmostEqual(result.residual_change_percent, 5.0, places=10)

    def test_prunes_points_outside_window(self) -> None:
        monitor = RollingRelativeMove(window_seconds=30, min_samples=3)
        for timestamp in (0.0, 10.0, 20.0, 40.0, 50.0):
            monitor.add(PricePoint(timestamp, 100.0 + timestamp, 200.0 + timestamp))

        self.assertGreaterEqual(monitor.points[0].timestamp, 10.0)
        self.assertEqual(monitor.points[-1].timestamp, 50.0)

    def test_rejects_non_increasing_timestamp(self) -> None:
        monitor = RollingRelativeMove(window_seconds=60, min_samples=3)
        monitor.add(PricePoint(10.0, 100.0, 200.0))
        with self.assertRaises(ValueError):
            monitor.add(PricePoint(10.0, 101.0, 201.0))


if __name__ == "__main__":
    unittest.main()

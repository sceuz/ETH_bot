"""Monitor ETH futures movement after removing the rolling BTC component."""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import logging
import math
import time
from typing import Iterable

import requests


BINANCE_FUTURES_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/price"
LOGGER = logging.getLogger("eth_relative_move")


@dataclass(frozen=True, slots=True)
class PricePoint:
    timestamp: float
    eth_price: float
    btc_price: float


@dataclass(frozen=True, slots=True)
class RelativeMove:
    beta: float
    eth_change_percent: float
    btc_change_percent: float
    residual_change_percent: float
    samples: int


class RollingRelativeMove:
    """Estimate ETH's BTC-adjusted move over a rolling time window.

    Beta is estimated from consecutive log returns. The residual movement is
    the window ETH log return minus beta times the window BTC log return.
    """

    def __init__(self, window_seconds: int = 3600, min_samples: int = 10) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if min_samples < 3:
            raise ValueError("min_samples must be at least 3")
        self.window_seconds = window_seconds
        self.min_samples = min_samples
        self._points: deque[PricePoint] = deque()

    @property
    def points(self) -> tuple[PricePoint, ...]:
        return tuple(self._points)

    def add(self, point: PricePoint) -> None:
        if point.eth_price <= 0 or point.btc_price <= 0:
            raise ValueError("prices must be positive")
        if self._points and point.timestamp <= self._points[-1].timestamp:
            raise ValueError("timestamps must increase")

        self._points.append(point)
        cutoff = point.timestamp - self.window_seconds
        while len(self._points) > 2 and self._points[1].timestamp < cutoff:
            self._points.popleft()

    def calculate(self) -> RelativeMove | None:
        if len(self._points) < self.min_samples:
            return None

        eth_returns = _log_returns(point.eth_price for point in self._points)
        btc_returns = _log_returns(point.btc_price for point in self._points)
        beta = _regression_beta(eth_returns, btc_returns)

        first = self._points[0]
        last = self._points[-1]
        eth_log_move = math.log(last.eth_price / first.eth_price)
        btc_log_move = math.log(last.btc_price / first.btc_price)
        residual_log_move = eth_log_move - beta * btc_log_move

        return RelativeMove(
            beta=beta,
            eth_change_percent=math.expm1(eth_log_move) * 100,
            btc_change_percent=math.expm1(btc_log_move) * 100,
            residual_change_percent=math.expm1(residual_log_move) * 100,
            samples=len(self._points),
        )


def _log_returns(prices: Iterable[float]) -> list[float]:
    values = list(prices)
    return [math.log(current / previous) for previous, current in zip(values, values[1:])]


def _regression_beta(asset_returns: list[float], market_returns: list[float]) -> float:
    if len(asset_returns) != len(market_returns) or not asset_returns:
        raise ValueError("return series must have the same non-zero length")

    market_mean = sum(market_returns) / len(market_returns)
    asset_mean = sum(asset_returns) / len(asset_returns)
    variance = sum((value - market_mean) ** 2 for value in market_returns)
    if variance <= 1e-18:
        return 0.0
    covariance = sum(
        (market - market_mean) * (asset - asset_mean)
        for asset, market in zip(asset_returns, market_returns)
    )
    return covariance / variance


class BinanceFuturesPriceSource:
    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "eth-relative-move-monitor/2.0"})

    def fetch(self) -> tuple[float, float]:
        response = self.session.get(
            BINANCE_FUTURES_TICKER_URL,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        prices = {
            item["symbol"]: float(item["price"])
            for item in response.json()
            if item.get("symbol") in {"ETHUSDT", "BTCUSDT"}
        }
        missing = {"ETHUSDT", "BTCUSDT"} - prices.keys()
        if missing:
            raise RuntimeError(f"Missing Binance symbols: {', '.join(sorted(missing))}")
        return prices["ETHUSDT"], prices["BTCUSDT"]


def run(args: argparse.Namespace) -> None:
    source = BinanceFuturesPriceSource(timeout_seconds=args.timeout)
    monitor = RollingRelativeMove(
        window_seconds=args.window_minutes * 60,
        min_samples=args.min_samples,
    )
    last_alert_direction = 0

    LOGGER.info(
        "monitor_started window_minutes=%s threshold_percent=%s interval_seconds=%s",
        args.window_minutes,
        args.threshold,
        args.interval,
    )

    while True:
        started_at = time.monotonic()
        try:
            eth_price, btc_price = source.fetch()
            monitor.add(PricePoint(time.time(), eth_price, btc_price))
            move = monitor.calculate()
            if move is None:
                LOGGER.info(
                    "warming_up samples=%s required=%s eth=%.2f btc=%.2f",
                    len(monitor.points),
                    args.min_samples,
                    eth_price,
                    btc_price,
                )
            else:
                LOGGER.info(
                    "relative_move residual=%.4f%% beta=%.4f eth=%.4f%% btc=%.4f%% samples=%s",
                    move.residual_change_percent,
                    move.beta,
                    move.eth_change_percent,
                    move.btc_change_percent,
                    move.samples,
                )
                direction = 1 if move.residual_change_percent >= args.threshold else -1 if move.residual_change_percent <= -args.threshold else 0
                if direction and direction != last_alert_direction:
                    LOGGER.warning(
                        "ETH BTC-adjusted movement crossed threshold: %.4f%%",
                        move.residual_change_percent,
                    )
                last_alert_direction = direction
        except (requests.RequestException, RuntimeError, ValueError) as error:
            LOGGER.error("price_update_failed error=%s", error)

        elapsed = time.monotonic() - started_at
        time.sleep(max(0.0, args.interval - elapsed))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-minutes", type=int, default=60)
    parser.add_argument("--threshold", type=float, default=1.0)
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--min-samples", type=int, default=10)
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    logging.basicConfig(
        level=getattr(logging, arguments.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        run(arguments)
    except KeyboardInterrupt:
        LOGGER.info("monitor_stopped")

"""Offline deterministic validation for Trading PRO's historic backtester."""
import copy
import os
import sqlite3
import tempfile
import unittest

from flask import Flask

from trading_pro import BOTS, register_trading_pro
from trading_backtest import (
    HOUR_MS, WARMUP_BARS, build_backtest_report, buy_and_hold,
    fetch_historical_bars, init_backtest_tables, load_report,
    simulate_strategy, store_report, validate_bars,
)


def trend_bars(count, origin=HOUR_MS * 1_000):
    bars = []
    for i in range(count):
        price = 100 + i * .12
        bars.append({
            "time": origin + i * HOUR_MS,
            "open": price,
            "high": price + .2,
            "low": price - .2,
            "close": price + .08,
        })
    return bars


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class BacktestingTests(unittest.TestCase):
    def test_fetch_30days_is_closed_and_paged_correctly(self):
        now_ms = HOUR_MS * 4_000 + HOUR_MS // 2
        queries = []

        def fake_get(url, params, timeout):
            self.assertEqual(params["interval"], "1h")
            self.assertLessEqual(params["limit"], 1000)
            queries.append(params.copy())
            starts = range(params["startTime"], params["endTime"] + 1,
                           HOUR_MS)
            rows = []
            for start in starts:
                if len(rows) >= params["limit"]:
                    break
                price = 100 + start / HOUR_MS / 10_000
                rows.append([
                    start, str(price), str(price + .5), str(price - .5),
                    str(price + .1), "100", start + HOUR_MS - 1,
                ])
            return FakeResponse(rows)

        bars = fetch_historical_bars("BTCUSDT", days=30,
                                     http_get=fake_get, now_ms=now_ms)
        self.assertEqual(len(bars), WARMUP_BARS + 30 * 24)
        self.assertEqual(len(queries), 1)
        self.assertEqual(bars[-1]["time"], now_ms // HOUR_MS * HOUR_MS - HOUR_MS)
        self.assertEqual(bars[-1]["close"], 100 + bars[-1]["time"] / HOUR_MS / 10_000 + .1)

    def test_fetch_60_days_paginates_without_double_count(self):
        now_ms = HOUR_MS * 4_000
        queries = []

        def fake_get(url, params, timeout):
            queries.append(params.copy())
            rows = []
            t = params["startTime"]
            while t + HOUR_MS - 1 <= params["endTime"] and len(rows) < params["limit"]:
                rows.append([t, "100", "101", "99", "100", "10", t + HOUR_MS - 1])
                t += HOUR_MS
            return FakeResponse(rows)

        bars = fetch_historical_bars("ETHUSDT", days=60,
                                     http_get=fake_get, now_ms=now_ms)
        self.assertEqual(len(bars), WARMUP_BARS + 60 * 24)
        self.assertEqual(len(queries), 2)
        self.assertEqual(queries[1]["startTime"],
                         queries[0]["startTime"] + 1000 * HOUR_MS)

    def test_missing_candle_and_invalid_price_are_rejected(self):
        bars = trend_bars(170)
        bars.pop(13)
        with self.assertRaisesRegex(ValueError, "Candles ausentes"):
            validate_bars(bars)
        bars = trend_bars(170)
        bars[12]["low"] = bars[12]["high"] + 2
        with self.assertRaisesRegex(ValueError, "OHLC"):
            validate_bars(bars)
        with self.assertRaises(ValueError):
            fetch_historical_bars("UNKNOWN", days=30, http_get=lambda *a, **k: None)

    def test_next_bar_execution_uses_only_prior_bars(self):
        bars = trend_bars(161)
        # Current candle plunges. A signal reading this candle would no
        # longer enter: a correct implementation enters on prior trend,
        # then the same-bar stop executes pessimistically.
        bars[-1] = {
            "time": bars[-1]["time"], "open": 120,
            "high": 121, "low": 60, "close": 62,
        }
        bot = next(b for b in BOTS if b["id"] == "trend")
        result = simulate_strategy(bot, bars, 160, 161)
        self.assertEqual(result["closed_trades"], 1)
        self.assertEqual(result["stop_exits"], 1)
        self.assertLess(result["return_pct"], 0)

    def test_stop_wins_when_stop_and_target_touched(self):
        bars = trend_bars(161)
        bars[-1] = {
            "time": bars[-1]["time"], "open": 120,
            "high": 800, "low": 60, "close": 500,
        }
        bot = next(b for b in BOTS if b["id"] == "trend")
        result = simulate_strategy(bot, bars, 160, 161)
        self.assertEqual(result["stop_exits"], 1)
        self.assertLess(result["return_pct"], 0)

    def test_fees_and_slippage_lower_flat_benchmark(self):
        bars = trend_bars(161)
        bars[-1] = {
            "time": bars[-1]["time"], "open": 120,
            "high": 120, "low": 120, "close": 120,
        }
        benchmark = buy_and_hold(bars, 160, 161)
        self.assertLess(benchmark["return_pct"], 0)
        self.assertGreater(benchmark["fees_usdt"], 0)

    def test_report_has_separate_70_30_periods_and_hashes(self):
        days = 14
        count = WARMUP_BARS + days * 24
        markets = {symbol: trend_bars(count)
                   for symbol in {bot["symbol"] for bot in BOTS}}
        report = build_backtest_report(days, markets)
        self.assertEqual(report["execution_mode"], "BACKTEST_ONLY")
        self.assertEqual(report["sections"]["evaluation"]["bars"] +
                         report["sections"]["validation"]["bars"], days * 24)
        self.assertEqual(len(report["strategies"]), 5)
        self.assertEqual(len(report["fingerprints"]), 3)
        for b in report["strategies"]:
            self.assertIsInstance(b["validation"]["return_pct"], float)
            self.assertLessEqual(len(b["validation"]["curve"]), 91)
        broken = copy.deepcopy(markets)
        broken["SOLUSDT"][-1]["time"] -= HOUR_MS
        with self.assertRaises(ValueError):
            build_backtest_report(days, broken)

    def test_report_persistence_is_user_scoped(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            db_path = os.path.join(tmp.name, "reports.sqlite3")

            def conn():
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                return c

            init_backtest_tables(conn)
            r = {"days": 14, "created_at": "2026-01-01T00:00:00Z",
                 "execution_mode": "BACKTEST_ONLY"}
            store_report(conn, 17, r)
            self.assertEqual(load_report(conn, 17, 14)["days"], 14)
            self.assertIsNone(load_report(conn, 18, 14))
            self.assertIsNone(load_report(conn, 17, 30))
        finally:
            tmp.cleanup()

    def test_backtest_post_needs_csrf_before_any_request(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            db_path = os.path.join(tmp.name, "web.sqlite3")
            def conn():
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                return c
            app = Flask(__name__)
            app.secret_key = "test-only"
            register_trading_pro(app, lambda f: f, conn)
            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["user_id"] = 10
                    sess["trading_pro_csrf"] = "good"
                self.assertEqual(
                    client.post("/trading-pro/api/backtest/run",
                                json={"days": 14}).status_code, 403)
                self.assertEqual(
                    client.post("/trading-pro/api/backtest/run",
                                json={"days": 999},
                                headers={"X-Trading-CSRF": "good"}).status_code, 409)
                response = client.get("/trading-pro/api/backtest/latest?days=30")
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json["report"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()

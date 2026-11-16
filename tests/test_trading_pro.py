"""Deterministic tests; no network, credentials or real orders."""
import os
import sqlite3
import tempfile
import unittest

from flask import Flask

from trading_pro import (
    BOTS, DAILY_LOSS_LIMIT, DRAWDOWN_LIMIT, INITIAL_CASH,
    execute_cycle, init_trading_tables, order_budget, register_trading_pro,
    rsi, strategy_signal,
)


def rising_bars(n=120):
    bars = []
    for i in range(n):
        price = 100.0 + i * 0.20
        bars.append({
            "time": i * 3_600_000,
            "open": price - 0.03,
            "high": price + 0.05,
            "low": price - 0.10,
            "close": price,
        })
    return bars


class TradingProTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = os.path.join(self.temp.name, "test.db")

        def connect():
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            return conn

        self.connect = connect
        init_trading_tables(self.connect)

    def tearDown(self):
        self.temp.cleanup()

    def markets(self, price_factor=1.0):
        bars = rising_bars()
        return {
            symbol: {
                "bars": bars,
                "price": bars[-1]["close"] * price_factor,
                "last_candle": bars[-1]["time"],
            }
            for symbol in {b["symbol"] for b in BOTS}
        }

    def test_order_budget_is_bounded(self):
        self.assertEqual(order_budget(10_000, 10_000, 0, .02), 1000)
        self.assertEqual(order_budget(10_000, 10_000, 4_000, .02), 0)
        self.assertEqual(order_budget(10_000, 10, 0, .02), 0)
        self.assertEqual(order_budget(float("nan"), 10_000, 0, .02), 0)

    def test_insufficient_candles_never_trade(self):
        for bot in BOTS:
            self.assertEqual(strategy_signal(bot["id"], rising_bars(50))[0], "HOLD")
        self.assertAlmostEqual(rsi([100] * 15), 50.0)

    def test_same_candle_is_idempotent(self):
        first = execute_cycle(self.connect, 9, self.markets())
        self.assertEqual(first["execution_mode"], "PAPER_ONLY")
        self.assertGreater(len(first["trades"]), 0)
        self.assertLessEqual(len(first["positions"]), 5)
        self.assertGreater(first["cash"], 0)
        second = execute_cycle(self.connect, 9, self.markets())
        self.assertEqual(len(first["trades"]), len(second["trades"]))
        self.assertEqual(len(first["positions"]), len(second["positions"]))

    def test_drawdown_triggers_circuit_breaker(self):
        opening = execute_cycle(self.connect, 11, self.markets())
        self.assertGreater(len(opening["positions"]), 0)
        result = execute_cycle(self.connect, 11, self.markets(price_factor=.65))
        self.assertEqual(result["mode"], "risk_paused")
        self.assertEqual(len(result["positions"]), 0)
        self.assertTrue(any(t["side"] == "SELL" for t in result["trades"]))
        self.assertLess(result["equity"], INITIAL_CASH)
        with self.assertRaises(ValueError):
            execute_cycle(self.connect, 11, self.markets())

    def test_stops_trigger_on_repeat_cycle_even_with_same_candle(self):
        opened = execute_cycle(self.connect, 88, self.markets())
        self.assertGreater(len(opened["positions"]), 0)
        stopped = execute_cycle(self.connect, 88, self.markets(price_factor=.96))
        self.assertEqual(stopped["mode"], "active")
        self.assertTrue(any(t["reason"] == "Stop-loss simulado"
                            for t in stopped["trades"] if t["side"] == "SELL"))
        self.assertLess(len(stopped["positions"]), len(opened["positions"]))

    def test_user_accounts_are_isolated(self):
        one = execute_cycle(self.connect, 101, self.markets())
        conn = self.connect()
        try:
            accounts = conn.execute("SELECT user_id FROM tp_accounts").fetchall()
            self.assertEqual([row["user_id"] for row in accounts], [101])
            self.assertTrue(one["positions"])
        finally:
            conn.close()

    def test_post_requires_csrf(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        register_trading_pro(app, lambda f: f, self.connect)
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = 1
                sess["trading_pro_csrf"] = "known-good-token"
            self.assertEqual(client.post("/trading-pro/api/pause").status_code, 403)
            self.assertEqual(client.post("/trading-pro/api/pause",
                                         headers={"X-Trading-CSRF": "forged"}).status_code, 403)
            result = client.post("/trading-pro/api/pause",
                                 headers={"X-Trading-CSRF": "known-good-token"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json["state"]["mode"], "paused")

    def test_safety_thresholds(self):
        self.assertLessEqual(DAILY_LOSS_LIMIT, .02)
        self.assertLessEqual(DRAWDOWN_LIMIT, .08)


if __name__ == "__main__":
    unittest.main()

"""Deterministic tests of Trading PRO 4.0 observability. No external network."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from flask import Flask

from trading_pro import BOTS, register_trading_pro
from trading_scheduler import HOUR_MS, run_scheduled_paper_cycle, set_auto_preference
from trading_observability import (
    PROBE_COOLDOWN_SECONDS, _age, get_monitor_report,
    market_diagnostic,
)


def fixture_markets():
    now = datetime.now(timezone.utc)
    end = (int(now.timestamp() * 1000) // HOUR_MS - 1) * HOUR_MS
    candles = []
    for n in range(120):
        price = 100.0 + n * .18
        candles.append({
            "time": end - (119 - n) * HOUR_MS,
            "open": price - .03,
            "high": price + .10,
            "low": price - .10,
            "close": price,
        })
    return {
        symbol: {
            "bars": [dict(c) for c in candles],
            "price": candles[-1]["close"],
            "last_candle": end,
        }
        for symbol in {b["symbol"] for b in BOTS}
    }


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        location = os.path.join(self.tmp.name, "simulated.sqlite3")

        def connection():
            c = sqlite3.connect(location)
            c.row_factory = sqlite3.Row
            return c

        self.db_conn = connection
        self.app = Flask(__name__)
        self.app.secret_key = "test-secret-for-ci"
        register_trading_pro(self.app, lambda f: f, self.db_conn)
        self.config = patch.dict(os.environ, {
            "TRADING_PRO_SCHEDULER_ENABLED": "1",
            "TRADING_PRO_CRON_SECRET": "testing-only-strong-" + "8" * 40,
        })
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.tmp.cleanup()

    def test_age_rejects_invalid_timestamps(self):
        self.assertIsNone(_age(None))
        self.assertIsNone(_age("invalid"))
        now = datetime(2026, 11, 15, tzinfo=timezone.utc)
        self.assertEqual(_age("2026-11-15T00:00:00+00:00", now), 0)

    def test_unconfigured_and_optin_are_not_fake_heartbeat(self):
        with patch.dict(os.environ, {
            "TRADING_PRO_SCHEDULER_ENABLED": "0",
            "TRADING_PRO_CRON_SECRET": "",
        }):
            set_auto_preference(self.db_conn, 5, True)
            report = get_monitor_report(self.db_conn, 5)
            self.assertEqual(report["state"], "server_not_configured")
            self.assertIsNone(report["last_snapshot_at"])
            self.assertFalse(report["scheduler_configured_on_server"])
        set_auto_preference(self.db_conn, 5, False)
        report = get_monitor_report(self.db_conn, 5)
        self.assertEqual(report["state"], "disabled")
        self.assertEqual(report["trades"]["executions"], 0)
        self.assertIsNone(report["trades"]["profit_factor"])

    def test_optin_without_job_is_not_marked_healthy(self):
        set_auto_preference(self.db_conn, 7, True)
        report = get_monitor_report(self.db_conn, 7)
        self.assertEqual(report["state"], "awaiting_first_run")
        self.assertIsNone(report["last_user_attempt_at"])

    def test_snapshots_and_user_scoped_realized_metrics(self):
        set_auto_preference(self.db_conn, 3, True)
        set_auto_preference(self.db_conn, 4, False)
        state = run_scheduled_paper_cycle(self.db_conn, market_fetch=fixture_markets)
        self.assertEqual(state["processed"], 1)
        report = get_monitor_report(self.db_conn, 3)
        self.assertEqual(report["execution_mode"], "PAPER_ONLY")
        self.assertEqual(report["state"], "recent_snapshot")
        self.assertEqual(len(report["daily"]), 1)
        self.assertGreater(report["trades"]["executions"], 0)
        self.assertEqual(len(report["strategies"]), 5)
        other = get_monitor_report(self.db_conn, 4)
        self.assertEqual(other["trades"]["executions"], 0)
        self.assertEqual(len(other["daily"]), 0)

    def test_stale_snapshot_is_explicit(self):
        set_auto_preference(self.db_conn, 8, True)
        run_scheduled_paper_cycle(self.db_conn, market_fetch=fixture_markets)
        older = datetime(2026, 11, 30, tzinfo=timezone.utc)
        report = get_monitor_report(self.db_conn, 8, now=older)
        self.assertEqual(report["state"], "stale")

    def test_market_probe_is_only_read_only_and_rate_limited(self):
        result, status = market_diagnostic(self.db_conn, 9, fetcher=fixture_markets)
        self.assertEqual(status, 200)
        self.assertTrue(result["result"]["no_orders_placed"])
        self.assertEqual(len(result["result"]["symbols"]), 3)
        second, status = market_diagnostic(self.db_conn, 9, fetcher=lambda: 1 / 0)
        self.assertEqual(status, 429)
        self.assertGreater(second["retry_after_seconds"], 0)
        conn = self.db_conn()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tp_trades").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tp_positions").fetchone()[0], 0)
        finally:
            conn.close()

    def test_market_probe_failure_does_not_invent_prices(self):
        result, status = market_diagnostic(
            self.db_conn, 10, fetcher=lambda: {
                "BTCUSDT": {"last_candle": 0, "price": 0, "bars": []},
            },
        )
        self.assertEqual(status, 503)
        self.assertFalse(result["ok"])
        self.assertNotIn("symbols", result["result"])
        monitor = get_monitor_report(self.db_conn, 10)
        self.assertEqual(monitor["market_probe"]["status"], "error")

    def test_invalid_zero_price_is_rejected_even_with_fresh_bars(self):
        markets = fixture_markets()
        markets["BTCUSDT"]["price"] = 0
        result, status = market_diagnostic(
            self.db_conn, 11, fetcher=lambda: markets,
        )
        self.assertEqual(status, 503)
        self.assertFalse(result["ok"])
        self.assertNotIn("symbols", result["result"])

    def test_monitor_api_requires_post_token_and_is_authenticated(self):
        # The standalone Flask test app has no base.html routes; the main app
        # handles full template render in GitHub Actions integration smoke.
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = 14
                sess["user_email"] = "test@example.test"
                sess["trading_pro_csrf"] = "test-csrf-token"
            self.assertEqual(
                client.post("/trading-pro/api/monitor/check-market").status_code, 403
            )
            read = client.get("/trading-pro/api/monitor")
            self.assertEqual(read.status_code, 200)
            self.assertEqual(read.json["monitor"]["state"], "disabled")

    def test_profit_metrics_are_net_of_buy_and_sell_fees(self):
        user = 77
        conn = self.db_conn()
        try:
            conn.execute(
                "INSERT INTO tp_trades (user_id,bot_id,symbol,side,quantity,"
                "price,fee,realized_pnl,reason,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (user, "trend", "BTCUSDT", "BUY", 1.0, 100, 1, 0, "fixture", "2026-01-01"),
            )
            conn.execute(
                "INSERT INTO tp_trades (user_id,bot_id,symbol,side,quantity,"
                "price,fee,realized_pnl,reason,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (user, "trend", "BTCUSDT", "SELL", 1.0, 112, 2, 9, "fixture", "2026-01-01"),
            )
            conn.execute(
                "INSERT INTO tp_trades (user_id,bot_id,symbol,side,quantity,"
                "price,fee,realized_pnl,reason,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (user, "trend", "BTCUSDT", "SELL", 1.0, 96, 1, -5, "fixture", "2026-01-02"),
            )
            conn.commit()
        finally:
            conn.close()
        report = get_monitor_report(self.db_conn, user)
        self.assertEqual(report["trades"]["closed_trades"], 2)
        self.assertEqual(report["trades"]["win_rate_pct"], 50)
        self.assertEqual(report["trades"]["realized_pnl_usdt"], 4)
        self.assertEqual(report["trades"]["fees_usdt"], 4)
        self.assertAlmostEqual(report["trades"]["profit_factor"], 1.8)


if __name__ == "__main__":
    unittest.main()

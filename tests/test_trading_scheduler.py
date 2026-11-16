"""Trading PRO v3: deterministic opt-in and cron tests. No internet access."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from flask import Flask

from trading_pro import BOTS, execute_cycle, register_trading_pro
from trading_scheduler import (
    HOUR_MS, acquire_lease, get_automation_report, init_scheduler_tables,
    release_lease, run_scheduled_paper_cycle, scheduler_configured,
    set_auto_preference, validate_fresh_markets,
)

STRONG_SECRET = "only-for-tests-" + "9" * 45


def fake_markets(now=None):
    now = now or datetime.now(timezone.utc)
    boundary = int(now.timestamp() * 1000) // HOUR_MS * HOUR_MS
    last = boundary - HOUR_MS
    series = []
    for n in range(120):
        price = 100.0 + n * .2
        series.append({
            "time": last - (119 - n) * HOUR_MS,
            "open": price - .05,
            "high": price + .1,
            "low": price - .15,
            "close": price,
        })
    return {
        symbol: {
            "price": series[-1]["close"],
            "bars": [dict(bar) for bar in series],
            "last_candle": last,
        }
        for symbol in {b["symbol"] for b in BOTS}
    }


class TradingSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db_path = os.path.join(self.temp.name, "test.sqlite3")

        def connection():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c
        self.db_conn = connection
        self.app = Flask(__name__)
        self.app.secret_key = "unique-test-secret"
        register_trading_pro(self.app, lambda f: f, connection)
        self.environment = patch.dict(os.environ, {
            "TRADING_PRO_SCHEDULER_ENABLED": "1",
            "TRADING_PRO_CRON_SECRET": STRONG_SECRET,
        })
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_safe_defaults_without_config(self):
        with patch.dict(os.environ, {
            "TRADING_PRO_SCHEDULER_ENABLED": "0",
            "TRADING_PRO_CRON_SECRET": "",
        }):
            self.assertFalse(scheduler_configured())
            with self.app.test_client() as client:
                r = client.post("/internal/trading-pro/cron")
                self.assertEqual(r.status_code, 404)

    def test_configuration_requires_long_secret(self):
        with patch.dict(os.environ, {"TRADING_PRO_CRON_SECRET": "short"}):
            self.assertFalse(scheduler_configured())
        self.assertTrue(scheduler_configured())

    def test_no_opt_in_no_market_fetch(self):
        set_auto_preference(self.db_conn, 7, False)
        with patch("trading_scheduler.fetch_markets",
                   side_effect=AssertionError("Unexpected market call")):
            result = run_scheduled_paper_cycle(self.db_conn)
        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["eligible"], 0)
        self.assertEqual(result["processed"], 0)

    def test_auto_cycle_is_idempotent_and_snapshots_recorded(self):
        set_auto_preference(self.db_conn, 1, True)
        set_auto_preference(self.db_conn, 2, False)
        markets = fake_markets()
        first = run_scheduled_paper_cycle(self.db_conn, market_fetch=lambda: markets)
        self.assertEqual(first["status"], "success")
        self.assertEqual(first["processed"], 1)
        conn = self.db_conn()
        try:
            trades_before = conn.execute(
                "SELECT COUNT(*) FROM tp_trades WHERE user_id=1"
            ).fetchone()[0]
            self.assertGreater(trades_before, 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM tp_trades WHERE user_id=2"
            ).fetchone()[0], 0)
        finally:
            conn.close()
        second = run_scheduled_paper_cycle(self.db_conn, market_fetch=lambda: markets)
        self.assertEqual(second["processed"], 1)
        conn = self.db_conn()
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM tp_trades WHERE user_id=1"
            ).fetchone()[0], trades_before)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM tp_daily_snapshots WHERE user_id=1"
            ).fetchone()[0], 1)
        finally:
            conn.close()

    def test_disabling_opt_in_prevents_new_scheduled_cycle(self):
        set_auto_preference(self.db_conn, 8, True)
        execute_cycle(self.db_conn, 8, fake_markets(), require_auto_enabled=True)
        set_auto_preference(self.db_conn, 8, False)
        with self.assertRaisesRegex(ValueError, "desativada"):
            execute_cycle(self.db_conn, 8, fake_markets(), require_auto_enabled=True)

    def test_paused_accounts_are_excluded(self):
        set_auto_preference(self.db_conn, 4, True)
        conn = self.db_conn()
        try:
            conn.execute("UPDATE tp_accounts SET mode='paused' WHERE user_id=4")
            conn.commit()
        finally:
            conn.close()
        result = run_scheduled_paper_cycle(self.db_conn, market_fetch=fake_markets)
        self.assertEqual(result["eligible"], 0)

    def test_stale_market_fails_closed(self):
        markets = fake_markets()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        validate_fresh_markets(markets, now_millis=now_ms)
        markets["BTCUSDT"]["last_candle"] -= HOUR_MS * 4
        with self.assertRaisesRegex(ValueError, "sincronia"):
            validate_fresh_markets(markets, now_millis=now_ms)

    def test_market_error_does_not_create_trades(self):
        set_auto_preference(self.db_conn, 10, True)
        markets = fake_markets()
        markets["BTCUSDT"]["last_candle"] -= HOUR_MS * 3
        result = run_scheduled_paper_cycle(self.db_conn, market_fetch=lambda: markets)
        self.assertEqual(result["status"], "market_error")
        self.assertEqual(result["processed"], 0)
        conn = self.db_conn()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tp_trades").fetchone()[0], 0)
        finally:
            conn.close()

    def test_exclusive_lease(self):
        token = acquire_lease(self.db_conn)
        self.assertIsNotNone(token)
        self.assertIsNone(acquire_lease(self.db_conn))
        release_lease(self.db_conn, token)
        self.assertIsNotNone(acquire_lease(self.db_conn))

    def test_cron_requires_exact_bearer_secret(self):
        with self.app.test_client() as client:
            self.assertEqual(client.post("/internal/trading-pro/cron").status_code, 401)
            self.assertEqual(client.post("/internal/trading-pro/cron",
                                         headers={"Authorization": "Bearer invalid"}).status_code, 401)
            valid = client.post("/internal/trading-pro/cron", headers={
                "Authorization": "Bearer " + STRONG_SECRET,
            })
            self.assertEqual(valid.status_code, 200)
            self.assertEqual(valid.json["job"]["status"], "idle")

    def test_settings_write_requires_session_csrf_and_boolean(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = 3
                sess["trading_pro_csrf"] = "csrf-abc"
            self.assertEqual(client.post("/trading-pro/api/automation/settings",
                                         json={"enabled": True}).status_code, 403)
            response = client.post("/trading-pro/api/automation/settings",
                                   json={"enabled": "true"},
                                   headers={"X-Trading-CSRF": "csrf-abc"})
            self.assertEqual(response.status_code, 400)
            response = client.post("/trading-pro/api/automation/settings",
                                   json={"enabled": True},
                                   headers={"X-Trading-CSRF": "csrf-abc"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json["automation"]["opted_in"])
            snapshot = client.get("/trading-pro/api/automation/status")
            self.assertEqual(snapshot.status_code, 200)
            self.assertTrue(snapshot.json["automation"]["will_run_when_triggered"])

    def test_report_isolated_and_labels_hypothetical_snapshots(self):
        set_auto_preference(self.db_conn, 20, True)
        run_scheduled_paper_cycle(self.db_conn, market_fetch=fake_markets)
        one = get_automation_report(self.db_conn, 20)
        other = get_automation_report(self.db_conn, 21)
        self.assertTrue(one["paper_only"])
        self.assertEqual(len(one["daily_snapshots"]), 1)
        self.assertEqual(len(other["daily_snapshots"]), 0)
        self.assertFalse(other["opted_in"])
        self.assertIn("Não são necessariamente fechamentos", one["snapshot_disclaimer"])


if __name__ == "__main__":
    unittest.main()

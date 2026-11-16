"""Offline tests of v5 deployment checks. Never access a real exchange."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

from trading_pro import register_trading_pro
from trading_deployment import (
    VERSION, _temporary_database, create_checkpoint, deployment_preflight,
    get_checkpoint,
)


class DeploymentPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self.tmp.name, "test.sqlite")
        def connect():
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            return conn
        self.db_conn = connect
        self.app = Flask(__name__)
        self.app.secret_key = "local-test-key-for-v5"
        register_trading_pro(self.app, lambda fn: fn, connect)

    def tearDown(self):
        self.tmp.cleanup()

    def test_version_ping_is_public_and_contains_no_secrets(self):
        with self.app.test_client() as client:
            response = client.get("/trading-pro/ping")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["version"], VERSION)
            self.assertEqual(response.json["execution_mode"], "PAPER_ONLY")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertNotIn("password", response.get_data(as_text=True).lower())
            self.assertNotIn("secret", response.get_data(as_text=True).lower())

    def test_default_secrets_are_flagged(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "MudeEssaSenha123!",
                                     "TRADING_PRO_SCHEDULER_ENABLED": "0",
                                     "TRADING_PRO_CRON_SECRET": ""}):
            self.app.secret_key = "mude-esta-chave-antes-de-colocar-online"
            preflight = deployment_preflight(self.app, self.db_conn, 8)
            self.assertEqual(preflight["status"], "needs_configuration")
            self.assertFalse(preflight["can_claim_live"])
            by_id = {c["id"]: c["state"] for c in preflight["checks"]}
            self.assertEqual(by_id["session_secret"], "blocker")
            self.assertEqual(by_id["admin_password_config"], "blocker")
            self.assertEqual(by_id["scheduler_endpoint"], "warning")

    def test_secure_env_does_not_claim_production_verified(self):
        self.app.secret_key = "safepass-" + "z" * 48
        with patch.dict(os.environ, {
            "ADMIN_PASSWORD": "great-different-admin-password-2026",
            "TRADING_PRO_SCHEDULER_ENABLED": "1",
            "TRADING_PRO_CRON_SECRET": "long-only-test-secret-" + "x" * 48,
        }):
            with patch("trading_deployment._temporary_database", return_value=False):
                preflight = deployment_preflight(self.app, self.db_conn, 1)
            self.assertEqual(preflight["status"], "preflight_passed_not_production_verified")
            self.assertFalse(preflight["can_claim_live"])
            self.assertEqual(preflight["execution_mode"], "PAPER_ONLY")
            self.assertFalse(preflight["blocking_check_ids"])

    def test_tmp_storage_warns_even_when_database_integrity_ok(self):
        self.app.secret_key = "a" * 45
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "test-secure-admin-setting-abc"}):
            preflight = deployment_preflight(self.app, self.db_conn, 55)
        self.assertEqual(preflight["database_location_kind"], "temporary")
        by_id = {c["id"]: c["state"] for c in preflight["checks"]}
        self.assertEqual(by_id["sqlite_integrity"], "pass")
        self.assertEqual(by_id["storage_location"], "blocker")

    def test_path_detector_distinguishes_ephemeral_from_non_temp(self):
        self.assertTrue(_temporary_database("/tmp/app/storage/app.sqlite"))
        self.assertTrue(_temporary_database("/dev/shm/state.sqlite"))
        self.assertFalse(_temporary_database("/data/persistent/app.sqlite"))
        self.assertIsNone(_temporary_database(""))

    def test_marker_is_stable_and_user_isolated(self):
        first = create_checkpoint(self.db_conn, 21)
        duplicate = create_checkpoint(self.db_conn, 21)
        other = create_checkpoint(self.db_conn, 22)
        self.assertEqual(first, duplicate)
        self.assertNotEqual(first["checkpoint_code"], other["checkpoint_code"])
        conn = self.db_conn()
        try:
            self.assertEqual(get_checkpoint(conn, 21), first)
            self.assertIsNone(get_checkpoint(conn, 23))
        finally:
            conn.close()

    def test_checkpoint_write_requires_existing_csrf(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = 11
                sess["trading_pro_csrf"] = "token-11"
            self.assertEqual(
                client.post("/trading-pro/api/deployment/checkpoint").status_code, 403
            )
            resp = client.post(
                "/trading-pro/api/deployment/checkpoint",
                headers={"X-Trading-CSRF": "token-11"}
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json["checkpoint"]["version_at_creation"], VERSION)
            read = client.get("/trading-pro/api/deployment")
            self.assertEqual(read.status_code, 200)
            self.assertEqual(
                read.json["preflight"]["checkpoint"]["checkpoint_code"],
                resp.json["checkpoint"]["checkpoint_code"]
            )
            self.assertNotIn("test.sqlite", read.get_data(as_text=True))

    def test_no_orders_or_trades_created_from_deployment_checks(self):
        create_checkpoint(self.db_conn, 13)
        deployment_preflight(self.app, self.db_conn, 13)
        with self.app.test_client() as client:
            self.assertEqual(client.get("/trading-pro/ping").status_code, 200)
        conn = self.db_conn()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tp_trades").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tp_positions").fetchone()[0], 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

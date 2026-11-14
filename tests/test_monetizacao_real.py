"""Regression tests for the isolated monetization hub (no production DB required)."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask
from monetizacao_real import register_monetizacao_real, _amount, _allowed_url


ROOT = Path(__file__).resolve().parents[1]


class MonetizacaoRealTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.db = Path(self.work.name) / "test.sqlite3"
        app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
        app.config.update(SECRET_KEY="unit-test-session-secret", TESTING=True)
        app.jinja_env.filters["money"] = lambda n: "R$ %.2f" % n
        endpoints = [
            "login", "dashboard", "premium_library", "product_new", "products",
            "professional_center", "venda_real_page", "copy_premium_page",
            "professional_funnel_page", "social_automation", "visual_studio_page",
            "commercial_robot", "offer_lab", "plan_30_days", "niches",
            "income_machine", "discipline_center", "bncc_center", "sales",
            "leads_page", "settings_page", "logout", "web_manifest", "favicon",
        ]
        for name in endpoints:
            app.add_url_rule("/stub/" + name, endpoint=name, view_func=lambda: "stub")
        def db_conn():
            conn = sqlite3.connect(str(self.db))
            conn.row_factory = sqlite3.Row
            return conn
        register_monetizacao_real(app, db_conn)
        self.app = app
        self.client = app.test_client()

    def tearDown(self):
        self.work.cleanup()

    def login(self, user_id=1):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
        resp = self.client.get("/monetizacao-real")
        self.assertEqual(resp.status_code, 200)
        with self.client.session_transaction() as sess:
            return sess["monetizacao_csrf"]

    def test_requires_login(self):
        resp = self.client.get("/monetizacao-real")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/stub/login", resp.location)

    def test_create_and_track_real_received_values(self):
        token = self.login()
        resp = self.client.post("/monetizacao-real", data={
            "csrf": token, "action": "create", "title": "Planner escolar original",
            "channel": "proprio", "reference_url": "https://example.org/produto",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Planner escolar original", resp.data)
        resp = self.client.post("/monetizacao-real", data={
            "csrf": token, "action": "metrics", "test_id": "1",
            "stage": "testando", "clicks": "30", "sales": "2",
            "received": "24.50", "cost": "4.25",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"20.25", resp.data)

    def test_rejects_missing_csrf_and_script_urls(self):
        self.login()
        self.assertEqual(self.client.post("/monetizacao-real", data={
            "action": "create", "title": "Produto seguro", "channel": "proprio",
        }).status_code, 400)
        with self.assertRaises(ValueError):
            _allowed_url("javascript:alert(1)")
        with self.assertRaises(ValueError):
            _amount("-10", "Valor")

    def test_other_user_cannot_edit_campaign(self):
        token = self.login(user_id=1)
        self.client.post("/monetizacao-real", data={
            "csrf": token, "action": "create", "title": "Primeiro produto",
            "channel": "proprio",
        })
        new_token = self.login(user_id=2)
        resp = self.client.post("/monetizacao-real", data={
            "csrf": new_token, "action": "metrics", "test_id": "1",
            "stage": "publicado", "clicks": "9", "sales": "1",
            "received": "10.00", "cost": "0",
        })
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(b"Primeiro produto", self.client.get("/monetizacao-real").data)


if __name__ == "__main__":
    unittest.main()

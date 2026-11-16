"""Offline tests for the public deployment verification script."""
import json
import unittest

from scripts.verify_trading_deployment import (
    REQUIRED_VERSION, check_base_url, verify_deployed_version,
)


class Response:
    def __init__(self, endpoint, code=200, body=None):
        self.status = code
        self.endpoint = endpoint
        self.body = body if body is not None else {
            "ok": True, "version": REQUIRED_VERSION,
            "execution_mode": "PAPER_ONLY",
        }
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def geturl(self):
        return self.endpoint
    def read(self, limit):
        return json.dumps(self.body).encode()[:limit]


class DeployVerifierTests(unittest.TestCase):
    def test_valid_version_and_no_trading_calls(self):
        seen = []
        def fake(request, timeout):
            seen.append((request.full_url, timeout))
            return Response(request.full_url)
        data = verify_deployed_version("https://demo.example.com", opener=fake)
        self.assertEqual(data["verified_deployment_version"], REQUIRED_VERSION)
        self.assertEqual(data["execution_mode"], "PAPER_ONLY")
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0][0].endswith("/trading-pro/ping"))

    def test_old_version_is_not_declared_deployed(self):
        def fake(request, timeout):
            return Response(request.full_url, body={
                "ok": True, "version": "4.0.0",
                "execution_mode": "PAPER_ONLY",
            })
        with self.assertRaisesRegex(ValueError, "não corresponde"):
            verify_deployed_version("https://demo.example.com", opener=fake)

    def test_broker_mode_cannot_be_accepted(self):
        def fake(request, timeout):
            return Response(request.full_url, body={
                "ok": True, "version": REQUIRED_VERSION,
                "execution_mode": "REAL_TRADING",
            })
        with self.assertRaises(ValueError):
            verify_deployed_version("https://demo.example.com", opener=fake)

    def test_html_or_redirect_not_accepted(self):
        def html(request, timeout):
            response = Response(request.full_url)
            response.read = lambda count: b"<html>login</html>"
            return response
        with self.assertRaisesRegex(ValueError, "JSON"):
            verify_deployed_version("https://demo.example.com", opener=html)
        def redirected(request, timeout):
            return Response("https://elsewhere.example.com/login")
        with self.assertRaisesRegex(ValueError, "200 direto"):
            verify_deployed_version("https://demo.example.com", opener=redirected)

    def test_rejects_unsafe_or_misleading_urls(self):
        for url in ("", "http://demo.example.com", "https://localhost",
                    "https://127.0.0.1", "https://10.0.0.1",
                    "https://demo.example.com/private",
                    "https://demo.example.com?token=123",
                    "https://u:p@demo.example.com"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    check_base_url(url)
        self.assertEqual(check_base_url("https://demo.example.com/"),
                         "https://demo.example.com")


if __name__ == "__main__":
    unittest.main()

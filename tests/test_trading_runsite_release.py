"""Static safety assertions for the manually triggered RunSite release workflow.

No RunSite credentials, network access or production deployment in tests.
"""
from pathlib import Path
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "runsite-trading-pro-release.yml"


class ControlledRunsiteDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.yaml = WORKFLOW.read_text(encoding="utf-8")

    def test_requires_manual_explicit_confirmation(self):
        self.assertIn("workflow_dispatch:", self.yaml)
        self.assertNotIn("\n  push:", self.yaml)
        self.assertNotIn("\n  schedule:", self.yaml)
        self.assertIn("disk_and_security_confirmed:", self.yaml)
        self.assertIn("default: false", self.yaml)
        self.assertIn('if [[ "$CONFIRMED" != "true" ]]', self.yaml)

    def test_fails_closed_without_secret_or_safe_url(self):
        self.assertIn("secrets.RUNSITE_API_TOKEN", self.yaml)
        self.assertIn('if [[ -z "$RUNSITE_API_TOKEN" ]]', self.yaml)
        self.assertIn("verify_trading_deployment import check_base_url", self.yaml)
        self.assertNotIn("ak_live_", self.yaml)
        self.assertIn("needs: preflight", self.yaml)

    def test_uses_official_cli_and_verifies_paper_only_marker(self):
        self.assertIn("https://runsite.app/install.sh", self.yaml)
        self.assertIn('runsite" service status "$DEPLOY_SERVICE"', self.yaml)
        self.assertIn('runsite" deploy "$DEPLOY_SERVICE"', self.yaml)
        self.assertIn('python scripts/verify_trading_deployment.py "$DEPLOY_BASE_URL"', self.yaml)
        self.assertIn("Trading PRO 5.0 (somente simulação)", self.yaml)

    def test_does_not_modify_runtime_secrets_or_insert_login(self):
        self.assertNotIn("runsite env set", self.yaml)
        self.assertNotIn("runsite login", self.yaml)
        self.assertNotIn("DROP TABLE", self.yaml)
        self.assertNotIn("TRADING_PRO_SCHEDULER_ENABLED=1", self.yaml)


if __name__ == "__main__":
    unittest.main()

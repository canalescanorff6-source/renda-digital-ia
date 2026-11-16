"""Static safety assertions for the manually triggered RunSite release workflow.

No RunSite credentials, network access or production deployment in tests.
"""
from pathlib import Path
import unittest
from uuid import UUID

from scripts.verify_runsite_release import check_service, live_deployment_matches

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
        self.assertIn('runsite" service status "$DEPLOY_SERVICE" --output json', self.yaml)
        self.assertIn('runsite" deploy "$service_id" --output json', self.yaml)
        self.assertIn('scripts.verify_runsite_release live', self.yaml)
        self.assertIn('python scripts/verify_trading_deployment.py "$DEPLOY_BASE_URL"', self.yaml)
        self.assertIn("Trading PRO 5.0 (somente simulação)", self.yaml)

    def test_does_not_modify_runtime_secrets_or_insert_login(self):
        self.assertNotIn("runsite env set", self.yaml)
        self.assertNotIn("runsite login", self.yaml)
        self.assertNotIn("DROP TABLE", self.yaml)
        self.assertNotIn("TRADING_PRO_SCHEDULER_ENABLED=1", self.yaml)


class RunsiteReleaseIdentityTests(unittest.TestCase):
    def test_service_must_match_repository_branch_and_url(self):
        service_id = UUID("c7d6b1e6-5dfb-4109-bd54-1852da59ec32")
        url = "https://example.runsite.app"
        service = {
            "id": str(service_id),
            "github_repo_full_name": "canalescanorff6-source/renda-digital-ia",
            "github_branch": "main",
            "url": url,
        }
        self.assertEqual(check_service(service, url), str(service_id))
        for field, value in (
            ("github_repo_full_name", "other/project"),
            ("github_branch", "preview"),
            ("url", "https://other.runsite.app"),
            ("id", "--help"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                check_service({**service, field: value}, url)

    def test_requires_new_deployment_live_at_expected_sha(self):
        sha = "a" * 40
        live = {"id": "expected", "commit_sha": sha, "is_live": True}
        self.assertTrue(live_deployment_matches({"deployments": [live]}, "expected", sha))
        for mismatch in ({"id": "previous"}, {"commit_sha": "b" * 40}, {"is_live": False}):
            with self.subTest(mismatch=mismatch):
                self.assertFalse(live_deployment_matches(
                    {"deployments": [{**live, **mismatch}]}, "expected", sha
                ))


if __name__ == "__main__":
    unittest.main()

"""Check RunSite CLI JSON before triggering and accepting a deployment."""
import argparse
import json
import sys
from uuid import UUID

from scripts.verify_trading_deployment import check_base_url

REPOSITORY = "canalescanorff6-source/renda-digital-ia"


def check_service(service, public_url):
    if (service.get("github_repo_full_name", "") or "").lower() != REPOSITORY:
        raise ValueError("O serviço selecionado está ligado a outro repositório.")
    if service.get("github_branch") != "main":
        raise ValueError("O serviço precisa estar ligado à branch main.")
    if check_base_url(service.get("url")) != check_base_url(public_url):
        raise ValueError("A URL informada diverge da URL canônica do serviço.")
    return str(UUID(service.get("id") or ""))


def live_deployment_matches(report, deployment_id, sha):
    return any(
        item.get("id") == deployment_id
        and item.get("is_live") is True
        and item.get("commit_sha") == sha
        for item in report["deployments"]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="check", required=True)
    commands.add_parser("service").add_argument("public_url")
    live = commands.add_parser("live")
    live.add_argument("deployment_id")
    live.add_argument("sha")
    args = parser.parse_args()
    try:
        report = json.load(sys.stdin)
        if args.check == "service":
            print(check_service(report, args.public_url))
            return 0
        return 0 if live_deployment_matches(report, args.deployment_id, args.sha) else 1
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"Verificação RunSite falhou ({type(exc).__name__}).", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

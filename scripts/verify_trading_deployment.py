#!/usr/bin/env python3
"""Verify the DEPLOYED public Trading PRO version without logging in or trading."""
import ipaddress
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

REQUIRED_VERSION = "5.0.0"


def check_base_url(raw):
    if not raw or not isinstance(raw, str):
        raise ValueError("Defina a URL HTTPS pública da hospedagem")
    value = urlsplit(raw.strip())
    if value.scheme != "https" or not value.hostname:
        raise ValueError("Apenas URLs HTTPS completas são permitidas")
    if value.username or value.password or value.query or value.fragment:
        raise ValueError("Não use credenciais, parâmetros ou fragmentos na URL")
    if value.path not in ("", "/"):
        raise ValueError("Use somente a URL base, sem caminho")
    if value.port not in (None, 443):
        raise ValueError("Use HTTPS na porta padrão 443")
    host = value.hostname.lower()
    if host in ("localhost",) or host.endswith((".local", ".internal")):
        raise ValueError("A URL deve apontar para uma hospedagem pública")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError("Endereços privados não são permitidos")
    except ValueError as exc:
        if "Endereços privados" in str(exc):
            raise
        # Domain names normally do not parse as IP literals.
    return urlunsplit(("https", value.netloc, "", "", ""))


def verify_deployed_version(base, opener=urlopen):
    endpoint = check_base_url(base) + "/trading-pro/ping"
    req = Request(endpoint, headers={"Accept": "application/json",
                                     "Cache-Control": "no-cache"})
    try:
        with opener(req, timeout=12) as resp:
            if resp.status != 200 or resp.geturl() != endpoint:
                raise ValueError("Resposta não é HTTP 200 direto da rota de versão")
            body = resp.read(2048)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise ValueError("Hospedagem não acessível: " + type(exc).__name__) from exc
    try:
        result = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("A rota não retornou JSON de versão válido") from exc
    if not isinstance(result, dict):
        raise ValueError("O formato do diagnóstico é inválido")
    if (result.get("version") != REQUIRED_VERSION
            or result.get("execution_mode") != "PAPER_ONLY"
            or result.get("ok") is not True):
        raise ValueError("Versão publicada não corresponde ao Trading PRO 5.0")
    return {"verified_deployment_version": REQUIRED_VERSION,
            "execution_mode": "PAPER_ONLY", "endpoint": endpoint}


def main():
    base = sys.argv[1] if len(sys.argv) == 2 else ""
    try:
        result = verify_deployed_version(base)
        print("DEPLOYMENT_VERIFIED: " + json.dumps(result))
        return 0
    except ValueError as exc:
        print("DEPLOYMENT_NOT_VERIFIED: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

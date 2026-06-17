#!/usr/bin/env python3
"""Teste de fumaça (smoke test) que exercita todos os endpoints do serviço.

Roda contra uma instância em execução (por padrão http://localhost:8000, ou
seja, `./dev.sh up`). Não usa dependências externas — apenas a stdlib.

Fluxo, encadeado para ser autossuficiente (cada passo usa o anterior):

    POST   /v1/group                       cria um grupo            -> 201
    GET    /v1/group                       lista grupos             -> 200
    GET    /v1/group/{id}                  detalha o grupo          -> 200
    PUT    /v1/group/{id}                  atualiza (com If-Match)  -> 200
    PUT    /v1/group/{id}/user/{uid}       adiciona membro          -> 201
    PUT    /v1/group/{id}/user/{uid}       de novo (idempotente)    -> 200
    GET    /v1/user/{uid}                  detalha o usuário        -> 200
    DELETE /v1/group/{id}/user/{uid}       remove membro            -> 204
    DELETE /v1/group/{id}                  remove o grupo           -> 204

Mais alguns casos negativos de autenticação/autorização. Sai com código != 0
se qualquer verificação falhar.

Uso:
    ./dev.sh up                     # suba o serviço primeiro
    python scripts/smoke_test.py
    python scripts/smoke_test.py --base-url http://localhost:8000 -v
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from typing import Any

# Serviços da allowlist (ver app/security.py).
WRITE_SERVICE = "assessment-service"  # permissão de escrita
READ_SERVICE = "report-service"       # somente leitura


class Client:
    """Cliente HTTP mínimo sobre urllib, com cabeçalhos de identidade."""

    def __init__(self, base_url: str, verbose: bool) -> None:
        self.base_url = base_url.rstrip("/")
        self.verbose = verbose

    def request(
        self,
        method: str,
        path: str,
        *,
        service_id: str | None = WRITE_SERVICE,
        user_id: int | None = 1,
        body: dict[str, Any] | None = None,
        if_match: str | None = None,
    ) -> tuple[int, Any, Any]:
        """Faz uma requisição e devolve (status, headers, corpo-json-ou-texto)."""
        url = f"{self.base_url}{path}"
        data = None
        headers: dict[str, str] = {"Accept": "application/json"}
        if service_id is not None:
            headers["X-Service-Id"] = service_id
        if user_id is not None:
            headers["X-User-Id"] = str(user_id)
        if if_match is not None:
            headers["If-Match"] = if_match
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            # Mantém o objeto de headers do urllib (busca case-insensitive); o
            # servidor emite os nomes em minúsculas (etag, location).
            with urllib.request.urlopen(req) as resp:
                status, raw, hdrs = resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as exc:
            status, raw, hdrs = exc.code, exc.read(), exc.headers
        except urllib.error.URLError as exc:
            print(f"\n✗ Não foi possível conectar a {url}: {exc.reason}")
            print("  O serviço está no ar? Tente: ./dev.sh up")
            sys.exit(2)

        parsed: Any
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw.decode(errors="replace")

        if self.verbose:
            print(f"    → {method} {path} [{status}]")
            if parsed is not None:
                print(f"      {json.dumps(parsed, ensure_ascii=False)[:200]}")
        return status, hdrs, parsed


class Runner:
    """Acumula verificações e relata o resultado."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        mark = "✓" if ok else "✗"
        line = f"  {mark} {label}"
        if not ok and detail:
            line += f"  — {detail}"
        print(line)
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        return ok

    def summary(self) -> int:
        total = self.passed + self.failed
        print(f"\n{self.passed}/{total} verificações passaram.")
        return 0 if self.failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test de todos os endpoints.")
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="URL base do serviço (padrão: http://localhost:8000).")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Mostra cada requisição/resposta.")
    args = parser.parse_args()

    c = Client(args.base_url, args.verbose)
    r = Runner()

    # IDs aleatórios para evitar colisão entre execuções.
    suffix = random.randint(1000, 9999)
    group_name = f"smoke-test-{suffix}"
    user_id = random.randint(100_000, 999_999)

    group_id: int | None = None
    print(f"Alvo: {c.base_url}\n")

    # 1. POST /v1/group --------------------------------------------------------
    status, hdrs, body = c.request(
        "POST", "/v1/group",
        body={"groupName": group_name, "description": "criado pelo smoke test"},
    )
    if r.check("POST   /v1/group  cria grupo (201)", status == 201, f"status={status} body={body}"):
        group_id = body.get("group_id") or body.get("groupId")
        r.check("       resposta traz group_id", group_id is not None, f"body={body}")
        r.check("       header Location presente", "Location" in hdrs)
        r.check("       header ETag presente", "ETag" in hdrs)

    if group_id is None:
        print("\n✗ Sem group_id não dá para seguir o fluxo encadeado.")
        return r.summary()

    # 2. GET /v1/group ---------------------------------------------------------
    status, _, body = c.request("GET", "/v1/group", service_id=READ_SERVICE)
    ok = status == 200 and isinstance(body, dict) and "items" in body and "meta" in body
    r.check("GET    /v1/group  lista grupos (200)", ok, f"status={status}")

    # 3. GET /v1/group/{id} ----------------------------------------------------
    status, hdrs, body = c.request("GET", f"/v1/group/{group_id}", service_id=READ_SERVICE)
    etag = hdrs.get("ETag")
    ok = status == 200 and etag is not None
    r.check("GET    /v1/group/{id}  detalha grupo (200 + ETag)", ok, f"status={status}")

    # 4. PUT /v1/group/{id} (com If-Match) -------------------------------------
    status, hdrs, body = c.request(
        "PUT", f"/v1/group/{group_id}",
        body={"description": "atualizado pelo smoke test"},
        if_match=etag,
    )
    r.check("PUT    /v1/group/{id}  atualiza c/ If-Match (200)", status == 200,
            f"status={status} body={body}")

    # 5. PUT /v1/group/{id}/user/{uid}  adiciona membro ------------------------
    status, hdrs, body = c.request("PUT", f"/v1/group/{group_id}/user/{user_id}")
    r.check("PUT    /v1/group/{id}/user/{uid}  adiciona membro (201)", status == 201,
            f"status={status} body={body}")

    # 6. PUT de novo  ->  idempotente (200) ------------------------------------
    status, _, _ = c.request("PUT", f"/v1/group/{group_id}/user/{user_id}")
    r.check("PUT    .../user/{uid}  idempotente (200)", status == 200, f"status={status}")

    # 7. GET /v1/user/{uid} ----------------------------------------------------
    status, hdrs, body = c.request("GET", f"/v1/user/{user_id}", service_id=READ_SERVICE)
    ok = status == 200 and isinstance(body, dict) and "groups" in body
    r.check("GET    /v1/user/{uid}  detalha usuário (200)", ok, f"status={status}")

    # 8. DELETE /v1/group/{id}/user/{uid} --------------------------------------
    status, _, _ = c.request("DELETE", f"/v1/group/{group_id}/user/{user_id}")
    r.check("DELETE /v1/group/{id}/user/{uid}  remove membro (204)", status == 204,
            f"status={status}")

    # --- Casos negativos de autenticação/autorização -------------------------
    status, _, _ = c.request("GET", "/v1/group", service_id=None)
    r.check("GET    /v1/group  sem X-Service-Id (401)", status == 401, f"status={status}")

    status, _, _ = c.request("GET", "/v1/group", user_id=None)
    r.check("GET    /v1/group  sem X-User-Id (401)", status == 401, f"status={status}")

    status, _, _ = c.request(
        "POST", "/v1/group", service_id=READ_SERVICE,
        body={"groupName": f"forbidden-{suffix}"},
    )
    r.check("POST   /v1/group  serviço read-only (403)", status == 403, f"status={status}")

    status, _, _ = c.request("GET", "/v1/group/999999999", service_id=READ_SERVICE)
    r.check("GET    /v1/group/{id}  inexistente (404)", status == 404, f"status={status}")

    # 9. DELETE /v1/group/{id}  (limpeza) --------------------------------------
    status, _, _ = c.request("DELETE", f"/v1/group/{group_id}")
    r.check("DELETE /v1/group/{id}  remove grupo (204)", status == 204, f"status={status}")

    return r.summary()


if __name__ == "__main__":
    sys.exit(main())

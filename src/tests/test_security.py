"""Testes da camada de segurança: autenticação e autorização."""

from __future__ import annotations


def test_missing_headers_returns_401(client):
    resp = client.post("/v1/group", json={"groupName": "Turma"})
    assert resp.status_code == 401
    assert resp.json()["errorCode"] == "AUTHENTICATION_REQUIRED"


def test_unknown_service_returns_401(client):
    resp = client.post(
        "/v1/group",
        json={"groupName": "Turma"},
        headers={"X-Service-Id": "hacker", "X-User-Id": "1"},
    )
    assert resp.status_code == 401
    assert resp.json()["errorCode"] == "UNKNOWN_SERVICE"


def test_malformed_user_id_returns_401(client):
    resp = client.post(
        "/v1/group",
        json={"groupName": "Turma"},
        headers={"X-Service-Id": "assessment-service", "X-User-Id": "abc"},
    )
    assert resp.status_code == 401
    assert resp.json()["errorCode"] == "AUTHENTICATION_REQUIRED"


def test_non_positive_user_id_returns_401(client):
    resp = client.post(
        "/v1/group",
        json={"groupName": "Turma"},
        headers={"X-Service-Id": "assessment-service", "X-User-Id": "0"},
    )
    assert resp.status_code == 401


def test_read_only_service_cannot_write(client, read_headers):
    resp = client.post("/v1/group", json={"groupName": "Turma ESII"}, headers=read_headers)
    assert resp.status_code == 403
    assert resp.json()["errorCode"] == "READ_ONLY_SERVICE"


def test_read_only_service_can_read(client, read_headers):
    resp = client.get("/v1/group", headers=read_headers)
    assert resp.status_code == 200

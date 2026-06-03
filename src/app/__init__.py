"""Fábrica da aplicação FastAPI e registro dos tratadores de erro.

Todos os erros — de domínio, de validação ou inesperados — são serializados no
schema `Erro` do contrato. Nada de stack trace ou detalhe interno vaza para o
chamador (importante por ser um serviço backend consumido por outros serviços).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import close_pool, init_pool
from .errors import ServiceError, build_error_body

log = logging.getLogger("user_group_service")


def create_app() -> FastAPI:
    """Cria o app com ciclo de vida e tratadores de erro.

    As rotas são declaradas em `main.py` sobre a instância retornada aqui.
    """
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_pool()
        try:
            yield
        finally:
            close_pool()

    app = FastAPI(title=settings.app_name, version="3.0.0", lifespan=lifespan)
    _register_error_handlers(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _handle_service_error(request: Request, exc: ServiceError) -> JSONResponse:
        body = build_error_body(
            status=exc.status,
            error_code=exc.error_code,
            message=exc.message,
            path=request.url.path,
            details=exc.details,
        )
        return JSONResponse(status_code=exc.status, content=body)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        # JSON ilegível ou parâmetros inválidos -> 400 MALFORMED_REQUEST (sem details).
        if _is_malformed_request(errors):
            return JSONResponse(
                status_code=400,
                content=build_error_body(
                    status=400,
                    error_code="MALFORMED_REQUEST",
                    message="A requisição é inválida ou está malformada.",
                    path=request.url.path,
                ),
            )
        # Corpo legível, mas com falhas semânticas de campo -> 422 com details[].
        return JSONResponse(
            status_code=422,
            content=build_error_body(
                status=422,
                error_code="VALIDATION_FAILED",
                message="A requisição não passou na validação de campos.",
                path=request.url.path,
                details=_to_error_details(errors),
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Registramos o erro internamente, mas devolvemos uma resposta genérica.
        log.exception("Erro inesperado em %s", request.url.path)
        body = build_error_body(
            status=500,
            error_code="INTERNAL_ERROR",
            message="Ocorreu um erro inesperado ao processar a requisição.",
            path=request.url.path,
        )
        return JSONResponse(status_code=500, content=body)


def _is_malformed_request(errors: list[dict]) -> bool:
    """True se o corpo é um JSON ilegível ou há parâmetro inválido (-> 400)."""
    for error in errors:
        if error["type"] == "json_invalid":
            return True
        loc = error["loc"]
        if loc and loc[0] in ("query", "path", "header"):
            return True
    return False


def _to_error_details(errors: list[dict]) -> list[dict[str, str]]:
    """Converte erros de validação de corpo do Pydantic em `Erro.details`."""
    details: list[dict[str, str]] = []
    for error in errors:
        field_parts = [str(part) for part in error["loc"] if part != "body"]
        details.append(
            {
                "field": ".".join(field_parts) or "body",
                "code": error["type"].upper(),
                "message": error["msg"],
            }
        )
    return details

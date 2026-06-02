"""Ponto de entrada do User & Group Service.

    uvicorn main:app                # produção (atrás do service mesh)
    python main.py                  # desenvolvimento local

Por padrão escutamos apenas em loopback (ver Settings.host): o serviço é
backend-only e a exposição é feita pela infraestrutura (mTLS / service mesh).
"""

from __future__ import annotations

import logging

from app import create_app
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)

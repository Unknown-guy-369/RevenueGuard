FROM ghcr.io/astral-sh/uv:0.12.5 AS uv

FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
COPY packages ./packages

RUN uv sync --frozen --no-dev --package revenueguard-api

RUN useradd --create-home --uid 10001 revenueguard
USER revenueguard

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "uvicorn", "revenueguard_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

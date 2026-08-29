FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.14-slim-bookworm

ARG GIT_REV=unknown

LABEL org.opencontainers.image.title="botchan" \
      org.opencontainers.image.description="Discord bot for managing pools of temporary voice channels" \
      org.opencontainers.image.revision="${GIT_REV}" \
      org.opencontainers.image.source="https://github.com/bdomars/botchan" \
      org.opencontainers.image.url="https://ghcr.io/bdomars/botchan"

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    BOTCHAN_GIT_REV="${GIT_REV}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin botchan

COPY --from=builder /app/.venv /app/.venv
COPY botchan ./botchan

USER 10001:10001

ENTRYPOINT ["python", "-m", "botchan"]

# syntax=docker/dockerfile:1
# Base images are pinned by digest (Dependabot keeps them current).

FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Exactly the locked versions, verified against the hashes in uv.lock; no dev tools.
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
LABEL org.opencontainers.image.source="https://github.com/25LioN52/MonoBudget-telegram" \
      org.opencontainers.image.description="Self-hosted Telegram bot: Monobank weekly/monthly spending budget" \
      org.opencontainers.image.licenses="MIT"
# No package manager at runtime; unprivileged user; private data directory.
RUN python -m pip uninstall --yes --quiet pip \
 && find / -xdev -perm /6000 -type f -exec chmod a-s {} + \
 && groupadd --system --gid 10001 monobudget \
 && useradd --system --uid 10001 --gid monobudget --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin monobudget \
 && mkdir -p /data && chown 10001:10001 /data && chmod 700 /data
COPY --from=build /app/.venv /app/.venv
COPY src /app/src
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data
WORKDIR /app
USER 10001:10001
VOLUME ["/data"]
# No ports: the bot only makes outgoing connections (Telegram long polling, Monobank).
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "-m", "monobudget", "healthcheck"]
ENTRYPOINT ["python", "-m", "monobudget"]
CMD ["run"]

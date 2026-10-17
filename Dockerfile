# TradeMare server image: dashboard + daily scheduler (`trademare serve`).
# Python 3.12 + uv; dependencies are pinned by uv.lock.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first (cached layer), then the project.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY config ./config
RUN uv sync --locked --no-dev

# data/ (prices, trading database, tokens) and secrets are mounted, never baked in.
VOLUME ["/app/data", "/app/logs"]
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "trademare", "serve", "--host", "0.0.0.0", "--port", "8000"]

# CottonMouth backend — API + agent-trace watcher + Bedrock investigate.
# Also serves as the image for the sample agent (override the command).
FROM python:3.11-slim

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    COTTONMOUTH_DISABLE_RELOAD=1 \
    COTTONMOUTH_DATA_DIR=/data

WORKDIR /app

# Install backend dependencies first for layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code.
COPY src ./src
COPY sdk ./sdk
COPY scripts ./scripts
COPY examples ./examples
# Policy-as-data: enforced by the agent, served by the backend governance UI.
COPY agent_policies.json ./

# Install the CottonMouth SDK into the venv so the sample agent can `import cottonmouth`.
RUN uv pip install ./sdk

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8150

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8150/api/health', timeout=2).status==200 else 1)"

CMD ["python", "-m", "src.main"]

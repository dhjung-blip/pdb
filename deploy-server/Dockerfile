FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime
RUN groupadd --gid 1001 mcp \
 && useradd --uid 1001 --gid mcp --no-create-home --shell /sbin/nologin mcp
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=mcp:mcp . .
RUN mkdir -p /app/outputs /app/logs \
 && chown -R mcp:mcp /app/outputs /app/logs
EXPOSE 8000
USER mcp
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1
CMD ["python", "-m", "server", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000", "--http-path", "/mcp", "--public-prefix", "/mcp"]

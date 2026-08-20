FROM python:3.12-slim

# Bytecode written at build time, not per-start; no .pyc clutter in the volume.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/indexnow.db \
    DEFAULT_UI_PORT=8787

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY indexnow_tool ./indexnow_tool
RUN pip install --no-cache-dir .

# The database is the only state. Mount a volume here or lose it on rebuild.
RUN mkdir -p /data && useradd --create-home --uid 10001 indexnow && chown indexnow /data
USER indexnow
VOLUME ["/data"]
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=4).status==200 else 1)"

# 0.0.0.0 inside the container is reachable from the network, so the app refuses
# to start unless AUTH_PASSWORD is set. That refusal is deliberate.
CMD ["indexnow", "serve", "--host", "0.0.0.0"]

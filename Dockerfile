FROM python:3.13.11-slim-bookworm

ARG GDW_SOURCE_SHA=UNAVAILABLE
LABEL org.opencontainers.image.revision="${GDW_SOURCE_SHA}" \
      org.opencontainers.image.source="https://github.com/szl-holdings/gdw-frontier"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GDW_LOG_DIR=/var/log/gdw \
    GDW_DATA_DIR=/var/lib/gdw \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-multiproc

WORKDIR /srv/gdw

RUN groupadd --system --gid 10001 gdw \
    && useradd --system --uid 10001 --gid gdw --home-dir /srv/gdw gdw \
    && mkdir -p /var/log/gdw /var/lib/gdw /var/backups/gdw \
    && chown -R gdw:gdw /srv/gdw /var/log/gdw /var/lib/gdw /var/backups/gdw

COPY pyproject.toml README.md ./
COPY app ./app
COPY ci ./ci
COPY gdw_x ./gdw_x
RUN python -m pip install --upgrade pip==26.1.2 \
    && python -m pip install .

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=6 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=2)"]

CMD ["python", "-m", "app"]

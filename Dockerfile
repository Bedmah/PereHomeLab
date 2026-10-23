FROM python:3.12-slim

ARG APP_VERSION=0.1.2
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="PereHomeLab" \
    org.opencontainers.image.source="https://github.com/Bedmah/PereHomeLab" \
    org.opencontainers.image.version=$APP_VERSION \
    org.opencontainers.image.revision=$VCS_REF

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=$APP_VERSION \
    TZ=Europe/Moscow \
    APP_DOWNLOAD_DIR=/download-app \
    APP_DOWNLOAD_FILENAME=PereHomeLabKiosk.exe

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/Europe/Moscow /etc/localtime \
    && echo Europe/Moscow > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY main.py ./main.py
COPY check_ha.py ./check_ha.py
COPY secret.env.example ./secret.env.example
COPY README.md ./README.md

RUN mkdir -p /app/data /app/logs /app/sound /download-app \
    && find /app -type d -name __pycache__ -prune -exec rm -rf {} + \
    && find /app -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

STOPSIGNAL SIGTERM

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

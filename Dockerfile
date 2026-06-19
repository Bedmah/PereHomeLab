FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Moscow

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
COPY sound ./sound
COPY main.py ./main.py
COPY check_ha.py ./check_ha.py
COPY secret.env.example ./secret.env.example
COPY README.md ./README.md

RUN mkdir -p /app/data /app/logs \
    && find /app -type d -name __pycache__ -prune -exec rm -rf {} + \
    && find /app -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py advanced_features.py apartment_phases.py telegram_i18n.py notification_features.py VERSION ./

RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin app

USER 10001:10001
ENV DATABASE_PATH=/data/voltage.sqlite3 \
    HEALTH_HOST=0.0.0.0

EXPOSE 8080

CMD ["python", "/app/app.py", "run"]

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY config ./config
COPY features.config ./features.config
COPY bot ./bot
RUN pip install --no-cache-dir ./config

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data /app/backups \
    && chown -R appuser:appuser /app

USER appuser

ENTRYPOINT ["assistant-bot"]
CMD ["bot"]

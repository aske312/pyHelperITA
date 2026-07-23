FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir ./config

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data /app/backups /app/logs \
    && chown -R appuser:appuser /app

USER appuser

ENTRYPOINT ["assistant-bot"]
CMD ["bot"]

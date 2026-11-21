# Multi-stage build: dependencies first (cacheable), app code last.
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUTOBAHN_USE_NVX=0

WORKDIR /app

RUN useradd --create-home --uid 1001 appuser \
    && mkdir -p /app/data /app/media /app/staticfiles \
    && chown -R appuser:appuser /app

COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile -r requirements.txt

COPY --chown=appuser:appuser . .

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import socket; s=socket.create_connection(('127.0.0.1', 8000), 2); s.close()" || exit 1

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "RestaurantRobot.asgi:application"]

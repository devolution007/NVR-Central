FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# wireguard-tools provides `wg`, used by the worker to read tunnel handshakes.
RUN apt-get update && apt-get install -y --no-install-recommends wireguard-tools && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Install dependencies first for layer caching; the app code is bind-mounted in development.
RUN mkdir -p app && touch app/__init__.py && pip install --no-cache-dir -e ".[dev]"

COPY . .
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

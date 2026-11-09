FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake tiktoken's vocab files into the image at build time (network access is available during
# `docker build`) so ingestion at runtime never needs outbound network access for tokenization.
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')"

COPY . .
RUN mkdir -p /app/storage

EXPOSE 8001
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8001"]


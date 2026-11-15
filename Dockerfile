FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EARTHSCOPE_CACHE_DIR=/app/data \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv sync --locked --no-dev
COPY backend/app ./backend/app
COPY scripts/build_global_power.py ./scripts/build_global_power.py
RUN uv run --locked --no-dev --with numcodecs==0.16.5 python scripts/build_global_power.py
RUN useradd --create-home earthscope \
    && chown -R earthscope:earthscope /app/data
USER earthscope
EXPOSE 8000
CMD ["sh", "-c", ".venv/bin/uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]

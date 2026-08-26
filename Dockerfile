# Shared image for the API and the ingestion service — same code, different command.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so code edits do not invalidate the wheel layer.
COPY pyproject.toml README.md ./
COPY services/__init__.py services/__init__.py
RUN pip install --upgrade pip && pip install ".[stack]"

COPY migrations/ migrations/
COPY services/ services/
COPY scripts/ scripts/
RUN pip install --no-deps -e .

EXPOSE 8000
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

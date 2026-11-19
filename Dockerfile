# Symlink/Drop-in for root docker build
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim AS runner

WORKDIR /app

RUN groupadd -r agentgroup && useradd -r -g agentgroup -s /bin/false agentuser

COPY --from=builder /root/.local /home/agentuser/.local
ENV PATH=/home/agentuser/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8080
ENV APP_ENV=production

COPY app/ /app/app/
COPY config/ /app/config/

RUN chown -R agentuser:agentgroup /app /home/agentuser

USER agentuser

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping')" || exit 1

CMD ["python", "-m", "uvicorn", "app.runtime:app", "--host", "0.0.0.0", "--port", "8080"]

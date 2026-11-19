FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn

COPY docker/mock_webhook_server.py /app/mock_webhook_server.py

EXPOSE 8081

CMD ["python", "mock_webhook_server.py"]

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY controlplane ./controlplane
COPY demo ./demo

ENV PYTHONUNBUFFERED=1

# Overridden per-service by docker-compose.yml's `command:`.
CMD ["python", "-m", "controlplane.run_all"]

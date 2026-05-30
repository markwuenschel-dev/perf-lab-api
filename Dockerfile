# perf-lab-api/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# System deps for asyncpg + building
RUN apt-get update && apt-get install -y \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Requirements first (Docker cache win)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy everything else
COPY . .

EXPOSE 8000

# Dev command with hot reload
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
FROM python:3.9-slim

WORKDIR /app

# Install git, which is required by gimie
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./

# Install project dependencies from pyproject.toml
RUN pip install --no-cache-dir .

# Copy the rest of the application's source code
COPY . .

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["gunicorn", "src.api:app", "--workers", "4", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:1234"]
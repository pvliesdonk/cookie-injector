FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY . .

ENV COOKIE_DIR=/cookies
ENV HEALTH_PORT=8081
ENV LOG_LEVEL=INFO

CMD ["python", "-m", "health.server"]

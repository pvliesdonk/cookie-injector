FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .
RUN playwright install chromium

COPY . .

ENV COOKIE_DIR=/cookies
ENV CONFIG_PATH=/config/sites.yaml
ENV LOG_LEVEL=INFO

CMD ["python", "-m", "refresh.main"]

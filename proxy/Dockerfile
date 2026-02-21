FROM mitmproxy/mitmproxy:latest

RUN pip install --no-cache-dir \
    tldextract \
    structlog

COPY addon.py /app/addon.py
COPY cookie_store.py /app/cookie_store.py

WORKDIR /app
ENV COOKIE_DIR=/cookies
ENV LOG_LEVEL=INFO

CMD ["mitmdump", "--listen-host", "0.0.0.0", "--listen-port", "8080", "-s", "/app/addon.py"]

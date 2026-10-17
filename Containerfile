FROM docker.io/library/python:3.14-alpine

RUN apk add --no-cache openssl \
    && addgroup -S -g 1000 hdhr \
    && adduser -S -D -H -u 1000 -G hdhr hdhr \
    && mkdir -p /certs \
    && chown hdhr:hdhr /certs

WORKDIR /app
COPY --chown=hdhr:hdhr . /app

ENV PYTHONUNBUFFERED=1 \
    HDHR_HTTP=false

USER hdhr
VOLUME ["/certs"]
EXPOSE 8443
STOPSIGNAL SIGINT

CMD ["python3", "/app/scripts/serve-https.py", "8443", "--cert", "/certs/cert.pem", "--key", "/certs/key.pem"]

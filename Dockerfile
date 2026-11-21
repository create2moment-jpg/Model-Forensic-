# Model Forensic — shared deployment image (see docs/deploy.md)
FROM node:20-bookworm-slim AS web
# Corporate TLS-inspecting proxies: put their root CA(s) as *.crt into deploy/ca/ (git-ignored) before building.
COPY deploy/ca/ /tmp/ca/
RUN (cat /tmp/ca/*.crt 2>/dev/null || true) > /tmp/extra-ca.pem
ENV NODE_EXTRA_CA_CERTS=/tmp/extra-ca.pem
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN ./node_modules/.bin/tsc -b && ./node_modules/.bin/vite build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MF_DATA_DIR=/data MF_WEB_DIST=/app/web/dist MF_CONFIG=/config/model-forensic.yaml \
    MF_AZURE_CONFIG=/config/azure.local.yaml MF_TEAM_CONFIG=/config/team.local.yaml
COPY deploy/ca/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates >/dev/null 2>&1 || true
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
RUN useradd --create-home --uid 10001 mf && mkdir -p /data /config && chown mf:mf /data
WORKDIR /app
COPY backend/ /app/backend/
RUN pip install --no-cache-dir "/app/backend[azure,server,notebook]"
COPY --from=web /src/web/dist /app/web/dist
USER mf
VOLUME ["/data", "/config"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health',timeout=4)"
CMD ["mf", "serve", "--host", "0.0.0.0", "--port", "8765"]

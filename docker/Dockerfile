FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 10001 idpvault \
    && command -v setpriv   # entrypoint drops root -> idpvault with it; fail the build if absent
WORKDIR /srv/idpvault

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY frontend ./frontend
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# No USER directive: the entrypoint starts as root (to self-seed the master key
# and fix volume ownership on first boot), then drops to idpvault (uid 10001).
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import httpx; httpx.get('http://localhost:8080/healthz').raise_for_status()"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]

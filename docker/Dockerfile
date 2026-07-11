FROM python:3.12-slim

RUN useradd -r -u 10001 idpvault
WORKDIR /srv/idpvault

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY frontend ./frontend

USER idpvault
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import httpx; httpx.get('http://localhost:8080/healthz').raise_for_status()"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

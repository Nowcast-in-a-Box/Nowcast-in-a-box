FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app
COPY ingestion/requirements.txt /tmp/ingestion.lock
RUN pip install --no-cache-dir -r /tmp/ingestion.lock
COPY adapters/cma_radar adapters/cma_radar
COPY adapters/__init__.py adapters/__init__.py
COPY ingestion ingestion
COPY interfaces interfaces
COPY utils utils
ENTRYPOINT ["python", "-m", "ingestion"]

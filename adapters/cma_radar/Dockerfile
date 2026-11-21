FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app
COPY adapters/runtime-requirements.txt /tmp/reader.lock
RUN pip install --no-cache-dir -r /tmp/reader.lock
COPY adapters/__init__.py adapters/__main__.py adapters/runtime.py adapters/
COPY adapters/cma_radar adapters/cma_radar
COPY interfaces interfaces
COPY utils utils
ENTRYPOINT ["python", "-m", "adapters.runtime"]

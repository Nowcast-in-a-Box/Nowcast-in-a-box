FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY adapters/cma_radar/pysteps_lk/requirements.txt /tmp/pysteps.lock
RUN pip install --no-cache-dir -r /tmp/pysteps.lock

COPY adapters/__init__.py adapters/__init__.py
COPY adapters/cma_radar/__init__.py adapters/cma_radar/__init__.py
COPY adapters/cma_radar/pysteps_lk adapters/cma_radar/pysteps_lk
COPY interfaces interfaces
COPY utils utils
RUN mkdir -p /opt/nib/pysteps && \
    cp adapters/cma_radar/pysteps_lk/algorithm-manifest.json /opt/nib/pysteps/algorithm-manifest.json

ENV PYTHONPATH=/app
ENTRYPOINT ["python", "-m", "adapters.cma_radar.pysteps_lk.run"]

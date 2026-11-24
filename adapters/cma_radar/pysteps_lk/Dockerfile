FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY adapters/cma_radar/pysteps_lk/requirements.txt /tmp/pysteps.lock
RUN pip install --no-cache-dir -r /tmp/pysteps.lock

COPY adapters/model_runner.py /app/model_run.py
COPY adapters/__init__.py adapters/__init__.py
COPY adapters/cma_radar/__init__.py adapters/cma_radar/__init__.py
COPY adapters/cma_radar/pysteps_lk adapters/cma_radar/pysteps_lk
COPY interfaces interfaces
COPY utils utils
COPY atlas/contracts/models/cma_radar/pysteps_lk.json atlas/contracts/models/cma_radar/pysteps_lk.json
RUN mkdir -p /opt/nib/pysteps && \
    cp adapters/cma_radar/pysteps_lk/algorithm-manifest.json /opt/nib/pysteps/algorithm-manifest.json

ENV PYTHONPATH=/app
ENV NIB_MODEL=pysteps_lk
ENV NIB_ADAPTER_MODULE=adapters.cma_radar.pysteps_lk.adapter
ENTRYPOINT ["python", "model_run.py"]

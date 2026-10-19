FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app
COPY containers/models/requirements.txt /tmp/model.lock
RUN pip install --no-cache-dir -r /tmp/model.lock
COPY containers/models/run.py /app/model_run.py
COPY adapters/__init__.py adapters/__init__.py
COPY adapters/sevir/__init__.py adapters/sevir/__init__.py
COPY adapters/sevir/nowcastnet adapters/sevir/nowcastnet
COPY adapters/cma_radar/__init__.py adapters/cma_radar/__init__.py
COPY adapters/cma_radar/nowcastnet adapters/cma_radar/nowcastnet
COPY interfaces interfaces
COPY utils utils
ENV NIB_MODEL=nowcastnet
ENV NIB_ADAPTER_MODULE=adapters.cma_radar.nowcastnet.adapter
ENTRYPOINT ["python", "model_run.py"]

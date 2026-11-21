FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app
COPY adapters/sevir/wadepre/requirements.txt /tmp/model.lock
RUN pip install --no-cache-dir -r /tmp/model.lock
COPY adapters/model_runner.py /app/model_run.py
COPY adapters/__init__.py adapters/__init__.py
COPY adapters/sevir/__init__.py adapters/sevir/__init__.py
COPY adapters/sevir/wadepre adapters/sevir/wadepre
COPY interfaces interfaces
COPY utils utils
COPY atlas/contracts/models/sevir/wadepre.json atlas/contracts/models/sevir/wadepre.json
ENV NIB_MODEL=wadepre
ENV NIB_ADAPTER_MODULE=adapters.sevir.wadepre.adapter
ENTRYPOINT ["python", "model_run.py"]

ARG BASE_IMAGE=python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
FROM ${BASE_IMAGE}
WORKDIR /app
ARG INSTALL_REQUIREMENTS=1
COPY adapters/cma_radar/wadepre/requirements.txt /tmp/model.lock
RUN if [ "$INSTALL_REQUIREMENTS" = "1" ]; then pip install --no-cache-dir -r /tmp/model.lock; fi
COPY --from=weights WADEPre.onnx /opt/nib/weights/WADEPre.onnx
RUN echo "55c396165f620934b1262eeac19997f14b00fae5f3a150015a5a84c9cbd6ee3b  /opt/nib/weights/WADEPre.onnx" | sha256sum -c -
COPY adapters/model_runner.py /app/model_run.py
COPY adapters/__init__.py adapters/__init__.py
COPY adapters/sevir/__init__.py adapters/sevir/__init__.py
COPY adapters/sevir/wadepre adapters/sevir/wadepre
COPY adapters/cma_radar/__init__.py adapters/cma_radar/__init__.py
COPY adapters/cma_radar/wadepre adapters/cma_radar/wadepre
COPY interfaces interfaces
COPY utils utils
COPY atlas/contracts/models/cma_radar/wadepre.json atlas/contracts/models/cma_radar/wadepre.json
COPY atlas/contracts/models/sevir/wadepre.json atlas/contracts/models/sevir/wadepre.json
ENV NIB_MODEL=wadepre
ENV NIB_ADAPTER_MODULE=adapters.cma_radar.wadepre.adapter
ENTRYPOINT ["python", "model_run.py"]

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ARG MODEL
ENV NIB_MODEL=${MODEL}
WORKDIR /app
COPY containers/models/requirements.txt /tmp/model.lock
RUN pip install --no-cache-dir -r /tmp/model.lock
COPY containers/models/run.py /app/model_run.py
COPY adapters/sevir/${MODEL} adapters/sevir/${MODEL}
COPY adapters/cma_radar/nowcastnet adapters/cma_radar/nowcastnet
COPY interfaces interfaces
COPY utils utils
ENTRYPOINT ["python", "model_run.py"]

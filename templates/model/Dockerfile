FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app
# Replace example_data/example_model after copying the template. Build at repo root.
COPY adapters/example_data/example_model/requirements.txt /tmp/model-requirements.txt
RUN pip install --no-cache-dir -r /tmp/model-requirements.txt
COPY interfaces interfaces
COPY utils utils
COPY containers/models containers/models
COPY contracts/models/example_data/example_model.json contracts/models/example_data/example_model.json
COPY adapters/example_data/example_model adapters/example_data/example_model
ENV NIB_MODEL=example_model
ENV NIB_ADAPTER_MODULE=adapters.example_data.example_model.adapter
ENTRYPOINT ["python", "-m", "containers.models.run"]

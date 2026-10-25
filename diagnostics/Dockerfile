# Build from the resources root: docker build -f diagnostics/Dockerfile -t nib-diagnostics:automatic-flow .
FROM node:22-bookworm-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d AS map-viewer
WORKDIR /viewer
COPY diagnostics/visualization/viewer/package*.json ./
RUN npm ci
COPY diagnostics/visualization/viewer ./
RUN npm run build:embed

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY diagnostics/requirements.txt /tmp/diagnostics.lock
RUN pip install --no-cache-dir -r /tmp/diagnostics.lock
COPY diagnostics/visualization /app/diagnostics/visualization
COPY diagnostics/verification /app/diagnostics/verification
RUN pip install --no-cache-dir --no-deps /app/diagnostics/visualization /app/diagnostics/verification
COPY diagnostics/*.py /app/diagnostics/
COPY --from=map-viewer /viewer/dist-embed /app/diagnostics/visualization/viewer/dist-embed
COPY diagnostics/examples /app/diagnostics/examples
COPY utils utils
COPY interfaces interfaces
ENV MPLCONFIGDIR=/tmp/matplotlib
ENTRYPOINT ["python", "-m", "diagnostics"]

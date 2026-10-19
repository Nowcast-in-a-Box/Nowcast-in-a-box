# Build from repository root: docker build -f diagnostics/Dockerfile -t nib-diagnostics:dev .
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app
COPY diagnostics/requirements.txt /tmp/diagnostics.lock
RUN pip install --no-cache-dir -r /tmp/diagnostics.lock
COPY diagnostics/visualization /app/diagnostics/visualization
COPY diagnostics/verification /app/diagnostics/verification
RUN pip install --no-cache-dir --no-deps /app/diagnostics/visualization /app/diagnostics/verification
COPY diagnostics/*.py /app/diagnostics/
COPY diagnostics/examples /app/diagnostics/examples
COPY utils utils
COPY interfaces interfaces
ENV MPLCONFIGDIR=/tmp/matplotlib
ENTRYPOINT ["python", "-m", "diagnostics"]

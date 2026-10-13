FROM python:3.12-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/diagnostics

COPY requirements.txt /opt/diagnostics/requirements.txt
RUN pip install --no-cache-dir -r /opt/diagnostics/requirements.txt

COPY . /opt/diagnostics

ENV PYTHONPATH=/opt/diagnostics
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "diagnostics"]

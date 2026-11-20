FROM ghcr.io/sinapse-inpe/irene-nowcast@sha256:b4f89946c9951a2895092c43588bce70c53708ef064fbb4a71b4d22872b13d03
WORKDIR /opt/nib
COPY utils /opt/nib/utils
COPY adapters/dpc_sri_geotiff /opt/nib/adapters/dpc_sri_geotiff
ENV PYTHONPATH=/opt/nib
ENTRYPOINT ["/opt/venv/bin/python", "-m", "adapters.dpc_sri_geotiff.ingest"]

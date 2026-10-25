# Maintainer-only data image. The final diagnostics image copies these layers.
FROM scratch
LABEL org.opencontainers.image.source=https://github.com/Nowcast-in-a-Box/Nowcast-in-a-box
COPY viewer /basemap/viewer
COPY metadata /basemap/metadata
COPY SHA256SUMS /basemap/SHA256SUMS

# IRENE GeoTIFF ensemble converter

This is a separate NiB converter product, not a modification of Adriano's
IRENE model image.  It reads the completed IRENE manifest and its 48 GeoTIFF
files, then registers `forecasts/irene/fields.npz` as a four-member Forecast
with shape `(member, time, y, x)`.

The Handler integration is intentionally pending a shared `kind: converter`
resource and ordered-stage support.  The converter itself is runnable now:

```text
docker run --rm -v <run>:/run -v <irene-output>:/source:ro \
  nib-irene-converter:local --run /run --input-dir /source
```

The source manifest, rather than filename parsing, supplies each member, valid
time, model version and weight checksum.

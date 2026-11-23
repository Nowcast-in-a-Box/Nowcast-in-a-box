# CMA WADEPre demo

This demo reuses the SEVIR-trained WADEPre ONNX weights as a CMA Radar model.
It is a cross-domain packaging demonstration, not a validated CMA forecast.
The adapter maps 0-75 dBZ to the SEVIR 0-1 input range, clamps the output, and
linearly resamples the native 5-minute leads to 6, 12, ..., 120 minutes. Values
after the native 100-minute lead repeat the last frame.

## Build the image

```sh
cp artifacts/sevir/wadepre/WADEPre.onnx artifacts/cma_radar/wadepre/WADEPre.onnx
docker build \
  --build-context weights=artifacts/cma_radar/wadepre \
  -f adapters/cma_radar/wadepre/Dockerfile \
  -t nib-wadepre:cma-demo-embedded .
docker save nib-wadepre:cma-demo-embedded -o artifacts/nib-wadepre-cma-demo-embedded.tar
```

Load the tar on the demo machine:

```sh
docker load -i nib-wadepre-cma-demo-embedded.tar
```

The image contains the weights and verifies the pinned SHA-256 at startup.

## Import and run

Start the Handler, open `http://127.0.0.1:8765/`, and import
`tests/handler/fixtures/hub-cma-wadepre-selection-v1.json` from Orders. Choose a
ready CMA window, then start the run. The order selects `nowcastnet` and
`wadepre`; the Handler runs them one after the other against the same window.

This local fixture substitutes for a Hub export. The current Hub cannot yet
build a catalog from the Atlas revision that contains `wadepre-cma`; use the
fixture until the Hub one-version catalog change lands.

## Results

Verification and visualization accept both model IDs. The run stage rail adds a
model sequence showing done/running/waiting for each model. The result viewer
shows one visualization card per model. Each bundle now contains
`comparison.png` with the latest input, observed target and first forecast lead;
the interactive map viewer remains forecast-only.

## Contract notes

- `atlas/catalog/products/wadepre-cma.yaml` v1 maps the runtime model `wadepre`
  to CMA observations and forecasts.
- `adapters/cma_radar/wadepre/runtime.yaml` declares the product and the
  embedded image.
- `atlas/contracts/models/cma_radar/wadepre.json` keeps the pinned weight URL
  and SHA-256 together with the CMA input/output contract.

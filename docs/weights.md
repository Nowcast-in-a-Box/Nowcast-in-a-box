# Model weights

Git stores pointers. Hugging Face stores blobs. The handler (or a fetch
step it runs) downloads those blobs into `artifacts/`. Inference
containers run with `--network none`, so they cannot download at run
time. They see the files through a bind mount.

Observations (SEVIR HDF5 and any later dataset) are not on Hugging Face.
They stay on the operator's machine and are mounted read-only as
`$NIB_DATA_ROOT`.

## Why not git

ONNX, checkpoints, and HDF5 are large, binary, and versioned on their
own schedule. Putting them in git (or git LFS) makes clones heavy and
reviews useless. A Model Container directory (layout still to be
decided) holds a `manifest.json`, in-container adapter code, and a
Dockerfile. Git does not hold the weight file.

No `*.onnx`, `*.ckpt`, `*.pt`, `*.h5` in the tree. `.gitignore` rejects
them, including anything dropped into `artifacts/`.

## Hugging Face layout

Maintainers create an organization on the Hub (proposed id:
`nowcast-in-a-box`) and one model repo per algorithm, for example
`nowcast-in-a-box/wadepre`.

Each Hub repo contains the export the container actually loads (ONNX for
the first path) plus a model card that names the license and the git
commit of the export script. Training checkpoints may live there too;
the runtime pointer names the file the container will load, not the
whole repo.

Pin a **commit SHA**, not `main`. Record a **SHA-256** of the file so a
silent Hub rewrite cannot ship a different weight under the same name.

Planned `manifest.json` fragment (not implemented yet):

```json
{
  "artifact_format": "onnx",
  "artifact": {
    "source": "huggingface",
    "repo": "nowcast-in-a-box/wadepre",
    "revision": "0123456789abcdef0123456789abcdef01234567",
    "path": "WADEPre.onnx",
    "sha256": "…"
  }
}
```

`revision` is a full git commit on the Hub repo. `path` is the file
inside that commit. Fetch fails if the digest does not match.

## Where files land

`artifacts/` on the host is the download cache: pinned weights, and
later docker config the handler needs to start a run. The directory is
in git; its contents are not.

Compose and the handler will mount the relevant file into the Model
Container. Baking the blob into the image at `docker build` is a
possible fallback. The default path is host-side `artifacts/` plus a
read-only mount, because runtime is `--network none`.

Local iteration can still use `~/.cache/huggingface`. That is a
developer convenience. It is not the runtime path.

## What we do not do

- Download during `docker run` / Compose up of an inference job
- Follow `main` on the Hub
- Accept a URL from the control panel or the YAML
- Fetch arbitrary adapter code

The Hub is an artifact store for files the catalog already named.

## Data

`$NIB_DATA_ROOT` on the host is the catalog of observations. Do not
upload SEVIR (or later radar/satellite archives) to the Hub as a
substitute for that mount. A data package in git is a `manifest.json`
plus an adapter, the same split as models.

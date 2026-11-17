# First-run onboarding: assessment and plan

Status: **plan for discussion, not implemented**. Written 2026-10-18 against the
B–D implementation on branches `feat/handler-lifecycle` (PR #27) and
`feat/automatic-diagnostics` (PR #29), plus Hub PRs #12/#14 (on base #10).
None of those PRs is assumed merged, and no public Hub deployment exists.

This document answers, from a clean machine: where does a first-time user get
NiB, what must they install, what must be downloaded, how many commands and
manual steps are needed, and what is still missing. All sizes below were measured
on the development machine; they are not download estimates for future releases.

## 1. Where the user would download it today

| Expected entry | Actual state |
|---|---|
| GitHub Releases with OS/arch Handler binaries | **0 releases**. The `release` workflow exists but is disabled (`if: false`, manual dispatch only). |
| README step "go to Releases and download" | Points at a page with no assets. |
| macOS launcher auto-download | Requests `releases/latest/download/nib-handler-darwin-<arch>`; fails with a clear "no handler binary" message. |
| Hub | Not published anywhere. It must be built and run from source, or hosted by the team later. |

**Blocker 1:** there is currently no downloadable Handler. The first-run path
cannot pass step 3 of the README. Either publish the six binaries (the cross-build
already produces them) or stop describing Releases as available.

## 2. What must be installed (macOS, current verified platform)

| Requirement | Why | Notes |
|---|---|---|
| Docker Desktop | every inference/diagnostics stage | install/start is manual; first launch may ask for privileges |
| GitHub access + `git` | clone Runtime and Hub, initialize `atlas` submodule | users currently need the source tree |
| Node.js 22.13+/npm 10 | build the Hub frontend | only if running Hub from source |
| Python 3.12 + `uv` | Hub backend | `uv sync --frozen`, no Conda needed |
| ~10 GB free disk | CMA demo data | plus ~1.4 GB images and ~0.5 GB artifacts |
| Firefox/Chrome/Safari | Hub + `nib://` confirmation | Firefox scheme flow is verified; other browsers are not |

A packaged release could remove Node/Python from the user's list by serving the
built Hub, but the Hub still has to be hosted somewhere.

## 3. Resources the user must obtain

| Resource | Size (measured) | Source today |
|---|---|---|
| CMA July 2026 CREF observations | **9.9 GB, 8554 `.bin` files** | not in the repository; provided separately to the demo operator |
| NowcastNet weight | **135 MB** | `artifacts/sevir/nowcastnet/NowcastNet.onnx`, copied to `artifacts/cma_radar/nowcastnet/`; checksum pinned in code/docs |
| Docker images | ingestion **373 MB**, NowcastNet **343 MB**, diagnostics **624 MB** on disk | must be built locally from `Dockerfile`s; no registry published |
| Hub frontend build | ~1.2 MB `dist/` | `npm run build` |

**Blocker 2:** weights and demo data are not downloadable from the project.
A first-time user cannot obtain the 9.9 GB dataset through the documented flow.

**Blocker 3:** no container registry. Three `docker build` commands are required
for the CMA demo, each needing the repository build context and network access
for base images/Python packages. (Later `--network none` only covers execution.)

## 4. Manual steps, measured

A careful macOS user who already has Docker and the source tree still needs:

```sh
# 1. get both repositories and the atlas submodule
git clone https://github.com/Nowcast-in-a-Box/Nowcast-in-a-box
git clone https://github.com/Nowcast-in-a-Box/NiB-HUB
git -C Nowcast-in-a-box submodule update --init --recursive

# 2. build the three required images
docker build -f ingestion/Dockerfile -t nib-ingestion:cma-demo .
docker build -f containers/models/Dockerfile --build-arg MODEL=nowcastnet -t nib-nowcastnet:cma-demo .
docker build -f diagnostics/Dockerfile -t nib-diagnostics:cma-demo .

# 3. copy the pinned weight
mkdir -p artifacts/cma_radar/nowcastnet
cp artifacts/sevir/nowcastnet/NowcastNet.onnx artifacts/cma_radar/nowcastnet/

# 4. obtain/extract the CMA data, then edit config.yaml:
#    paths.data_root, runtime.dataset=cma_radar, allowed_origins, atlas_revision

# 5. start the Hub (source mode)
cd NiB-HUB/backend && uv sync --frozen
uv run uvicorn nib_hub.app:create_app --factory --host 127.0.0.1 --port 8000
cd ../frontend && npm ci && npm run build   # dev alternative: npm run dev

# 6. start the Handler once (registers nib:// and background service)
bin/macos/start.command start
```

Then in the browser: open the Hub → select Local → connect → confirm the browser
`nib://` prompt and local-network permission → choose data/model/diagnostics →
Validate → check local resources → Start → wait → Runs → Open result.

Counted honestly: **~10 terminal/manual actions before the browser**, of which
Docker installation, data acquisition, weight placement and configuration editing
are not scripted. Add Docker Desktop setup and the browser confirmations. A
developer with everything preinstalled can do it in ~20–30 minutes plus a
~5–10 minute first inference/diagnostics run; a new user needs substantially
longer, dominated by the 9.9 GB data and container builds.

## 5. What the current UI does well for a first run

- Local/Cloud is explicit; Cloud does not pretend to execute.
- Local connects before the Catalog, so the user does not configure a whole run
  and only then discover the Handler is unreachable.
- The 8-second connection budget is separate from Docker/data readiness, and the
  page distinguishes "not connected" from "connected but not ready".
- Validation, local resource preflight, single Start, unified Runs observation
  and read-only results remove the earlier "Open results starts computation"
  surprise.
- Failures keep drafts/forecasts; partial diagnostics remain visible.
- macOS background persistence avoids keeping a terminal open.

## 6. Blockers and failure points to fix first

| Priority | Gap | Minimal improvement |
|---|---|---|
| P0 | No Release binaries/images | enable the release workflow on a tag and publish the six binaries; publish the three CMA images (or a Compose bundle) to a registry |
| P0 | No obtainable demo data/weight | publish a downloadable, licensed demo data set (or a small synthetic sample) and weight-download/checksum instructions in one place |
| P1 | No hosted Hub | deploy the single-process Hub at a stable origin and add that origin to the shipped Handler config; document the exact URL |
| P1 | Users must build Hub from source | ship a built `dist/` or a container image; remove Node/Python from the end-user path |
| P1 | README promises a Release that does not exist | make README/launcher messaging match reality until P0 is done |
| P2 | Linux/Windows only foreground | add per-platform background/scheme behavior after native testing; do not claim parity |
| P2 | Browser support beyond Firefox unknown | test Chrome/Safari `nib://` and local-network behavior; document what each shows |
| P2 | Trusted origins are localhost-only | ship the real deployment origin; keep exact-origin validation |
| P3 | No single "doctor" command | a read-only preflight that prints Docker/image/weight/data/config status before the browser flow |

## 7. Who should do what

- **Release automation (maintainers):** build/publish Handler binaries, images,
  checksums and the demo data/weight location; host Hub; keep a versioned
  configuration with the real Hub origin.
- **User (until packaging improves):** install Docker, download the release/data,
  run the launcher, confirm browser prompts, choose products, validate/start.
- **User should not have to:** install Go, build the Handler, edit YAML to add
  the official Hub, copy tokens, or guess ports.

## 8. Recommended next step

Do not add an installer yet. First make the documented path true by publishing
the three artifacts (Handler binaries, images, demo data/weight), then host the
Hub at a real origin. After that, re-walk this exact plan on a clean machine and
record the measured time, downloads and failure recovery. Only then decide
whether a desktop installer or container bundle is justified.

## Limits of this assessment

Measured only on the development macOS machine, with Docker and all source trees
already present. The 9.9 GB data set, container builds, Hub hosting and browser
behaviour for non-Firefox browsers were not exercised from a clean user account.
No public deployment origin exists, so the documented `allowed_origins` remains
localhost-only. Linux/Windows native service behavior is unverified.

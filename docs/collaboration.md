# Collaboration

Work is tracked as GitHub issues. The issue number is the id that
branches, commits, and pull requests use (`feat/12-…`, `(#12)`,
`Closes #12`). GitHub links those automatically.

[`modules.yaml`][modules] is a planning map of the system (handler,
compose, contracts, …). It is not the id you put in git. When you open
an issue, you may mention the area in the body so people can find
related work.

Admin steps (labels, branch protection) are in [github-setup.md][gh].

## What runs where

The v5 diagram splits host from containers.

| Where | What |
| --- | --- |
| User's machine | Handler binary from CI / Releases. Linux, macOS, Windows. |
| User's machine | `review/` page: evaluation + visualization |
| Local Docker Engine | One Model Container per algorithm |
| Host disk, read-only | Observations (`NIB_DATA_ROOT` → `/inputs/data`) |
| `artifacts/` | Downloaded weights and docker config |
| Named volume / `workspace/` | Run outputs (gitignored) |
| Hugging Face | Weight blobs, fetched into `artifacts/` |
| NiB Hub (separate product) | Selection and configuration only |

Adapters are packaging-time. They are not a host Python library for
inference.

## Principles

1. **Issue, then code.** File or claim an issue, get it assigned, then
   branch. Unassigned work on `main` will be rejected.
2. **One issue, one PR.** The issue number is the handle everywhere.
3. **Host stays thin.** Inference belongs in images. Metrics and
   rendering belong in `review/`. Do not add a repository-root Python
   package.
4. **Contract changes are expensive.** Schemas under `contracts/` need
   review from people whose images consume them.
5. **`main` stays releasable.** Cross-compile stays green. Handler tests
   on Linux, macOS, and Windows will gate merges once they are ready.
6. **English** in issues, PRs, and comments.
7. **No binaries in git.** `artifacts/` is a download cache, not a
   commit target.
8. **Errors fail closed.** No invented data path, no fake CSI.
9. **Tests live with the code that runs them.** Handler tests in
   `handler/`. Container tests run in that image.

## How to claim work

1. Skim [`modules.yaml`][modules] so you are not duplicating an area
   someone already opened an issue for.
2. Open [Claim work][claim] (or a [bug][bug]). GitHub assigns the
   number (`#12`).
3. A maintainer **assigns** the issue to you.
4. Branch `feat/12-short-slug` and mention `#12` in commits and the PR,
   as in [CONTRIBUTING.md][contrib].

## Suggested order of work

Open issues in roughly this order; the numbers will be whatever GitHub
gives you.

1. Handler (host daemon; already in tree, more slices later)
2. Compose invocation from the handler
3. Shared contracts (YAML / manifest schemas)
4. Hugging Face weight fetch into `artifacts/`
5. First Model Container
6. Data mount + first data package
7. `review/` page (evaluation + visualization)
8. Further Model Containers

[modules]: modules.yaml
[gh]: github-setup.md
[claim]: ../.github/ISSUE_TEMPLATE/claim-module.yml
[bug]: ../.github/ISSUE_TEMPLATE/bug.yml
[contrib]: ../CONTRIBUTING.md

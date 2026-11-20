# Branch consolidation — 2026-10-15

The user requested that existing development branches be integrated into main and retired before new work starts on a fresh branch.

## Runtime decisions

| Branch group | Disposition | Evidence |
| --- | --- | --- |
| `docs/contributing` | Merge the contribution tutorial and issue forms | Two previously unpublished documentation commits; preserve container boundaries and Python formatting guidance. |
| `feat/atlas-pin`, `feat/runtime-integration`, `refactor/handler-runtime-layout`, `feat/diagnostics-main`, `feat/unified-tests` | Record existing delivery history, retain main's evolved files | Patch-equivalent changes were delivered in PRs #11, #13, #15, #17, and #19. |
| `feat/multi-model-handler` | Record existing delivery history, retain main | All 26 changed paths match main after squash PR #20. |
| `feat/diagnostics`, `feat/diagnostics-handoff`, `feat/diagnostics-runtime-handoff` | Record history without restoring the old runtime | Visualization source was delivered in PR #13; the 11 old module-local tests moved to `tests/visualization`. Docker configuration and launchers were subsequently updated. |
| `feat/models` | Record history, retain current adapters | 66 changed files match main exactly; the other 10 received later Zarr, stored-field, dependency, or configuration changes. |
| `joking233-patch-1`, `joking233-patch-2`, `joking233-patch-3` | Record history, retain current implementations | The first two Zarr deliveries match main's changed files; the tests are patch-equivalent to PR #9. |
| `feat/data-adapter` | Record retirement of the original prototype | The `nib_data_loaders_minimal_v1` prototype was replaced by `adapters/`; restoring its old directory would duplicate the implementation. |
| `feat/init` | Record retirement of the original layout | Handler code evolved; root Go module and `tests/handler` replaced the nested module and tests. The old `review` directory was replaced by diagnostics. |
| Already-ancestor branches | No source merge needed | `feat/handler-init`, `feat/diagnostics-stage`, `refactor/handler-layout`, `pilot/github-issue-sync`, and `fix/report-graphql-errors` were already ancestors. |

An explicit `ours` history merge was used only for the audited superseded runtime branches. It preserves main's source tree rather than reintroducing old layouts or undoing delivered work. Repository rules require a PR and do not allow merge commits on the PR merge path, so the original branch histories are retained in the integration PR and local backup bundles; main receives the verified consolidated tree through a squash merge.

## Other repositories

Hub's `dev` contains genuine unpublished changes: evaluation exports, editable schema defaults, single-current-version catalog validation, and optional same-origin frontend delivery. These require a normal source merge with the newer multi-model and local-run flow. Other Hub feature branches and all atlas branches were already ancestors of main.

## Local work

Before consolidation, branch bundles, patches, and hashed copies of 230 local files from 12 worktrees were saved under the gitignored `.pi/backups/branch-consolidation-20261015/`. Dirty worktree files and the original Hub `backend/uv.lock` must not be discarded during branch cleanup. Old draft work is preserved separately rather than overlaid onto main.

Going forward, create a branch from updated main for one task, merge the completed work, then delete its branch. Do not leave completed delivery branches as competing development baselines.

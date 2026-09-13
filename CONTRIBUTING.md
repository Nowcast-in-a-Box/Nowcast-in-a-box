# Contributing

This repository is the Nowcast-in-a-Box local runtime. The host program
is the handler. Model code runs in Docker. Evaluation and visualization
run on the host from `review/`.

Read this before you write code. Maintainers: [GitHub setup][gh].

Further reading:

- [Development][dev]
- [How we work][collab]
- [Weights on Hugging Face][weights]
- [Architecture][docs]

## Git workflow

GitHub Flow. One long-lived branch: `main`.

The work id is the GitHub issue number, not a home-grown code like `E1`
or `C2`. GitHub turns `#12` in commits and PRs into a link.

```text
main
  └── feat/12-status-docker     your branch (12 is the issue number)
        └── pull request → main   body contains Closes #12
```

Do not create `develop` or `release/*`. Do not push commits to `main`.

### Branch names

```text
<type>/<issue-number>-<short-slug>
```

`type` is the same set as commit types below. Examples:
`feat/12-uri-scheme`, `fix/15-loopback-windows`, `docs/18-weights`.

Do not put `#` in the branch name. The number is enough.

One branch, one issue. If the work splits, open a new issue and a new
branch.

### Commit messages

We follow [Conventional Commits][conv]. Every commit on a branch, and
the PR title (which becomes the squash commit on `main`), uses:

```text
<type>(<scope>): <summary> (#<issue>)
```

`scope` is optional. `(#<issue>)` is not.

```text
feat: report docker daemon version on /v1/status (#12)
fix(handler): reject 0.0.0.0 on windows as well (#15)
docs: describe branch protection (#18)
```

| Type | When |
| --- | --- |
| `feat` | New behaviour |
| `fix` | A bug |
| `docs` | Documentation only |
| `test` | Tests only |
| `ci` | GitHub Actions |
| `chore` | Tooling, ignore files, busywork |
| `refactor` | Code change with no behaviour change |

`scope` is a short area when it helps: `handler`, `review`, `ci`,
`docs`, `scripts`. Leave it off when the change is obvious from the
summary.

Summary:

- Imperative: `add` / `reject` / `report`, not `added` or `adds`
- English, lowercase after the colon, no trailing period
- Roughly 72 characters for the whole first line
- One change. Unrelated work is a second commit or a second issue.

Body is optional. Use it for why, not a restatement of the diff. Wrap
at 72 characters.

Footer: the PR body has `Closes #12` so GitHub closes the issue on
merge. Individual commits only need `(#12)` in the subject.

Breaking change (rare): `feat(handler)!: drop YAML v0 run config (#40)`
and a `BREAKING CHANGE:` footer explaining what callers must do.

Squash-merge uses the PR title as the commit on `main`. Put `(#12)` in
that title too.

### How to send a change

1. Open an issue ([claim][claim] or [bug][bug]) and wait until it is
   assigned to you. That issue's number is the id for the branch,
   commits, and PR.
2. `git fetch origin && git checkout main && git pull`
3. `git checkout -b feat/12-short-slug` (use your issue number)
4. Open a pull request against `main`. Title:
   `feat: short description (#12)`. Body: `Closes #12`.
5. Wait for review. CI cross-compiles Linux, macOS, and Windows
   binaries and uploads them as the `nib-handler` artifact. Maintainers
   squash-merge. Delete the branch.

External contributors fork first, then the same steps.

Handler contributors who want to run tests locally: `cd handler && go
test ./...`. That is optional until the CI test job is turned on. Users
of the handler do not install Go; they download the CI or release
binary.

Do not install extra git hook frameworks. Reviewers check the commit
and PR title against the format above.

## What not to commit

Weights, checkpoints, ONNX, HDF5, run PNGs, PDF, Word, Visio, `workspace/`,
contents of `artifacts/`, secrets, `.env`, a root `pyproject.toml` /
`uv.lock`. See [weights.md][weights].

[gh]: docs/github-setup.md
[dev]: docs/development.md
[collab]: docs/collaboration.md
[weights]: docs/weights.md
[docs]: docs/
[claim]: .github/ISSUE_TEMPLATE/claim-module.yml
[bug]: .github/ISSUE_TEMPLATE/bug.yml
[conv]: https://www.conventionalcommits.org/en/v1.0.0/

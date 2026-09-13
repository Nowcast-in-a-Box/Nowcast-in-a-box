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

The work ID is the GitHub issue number. GitHub turns `#12` in commits and PRs into a link.

```text
main
  └── feat/status-docker     your branch
        └── pull request → main   body contains Closes #12
```

Do not create `develop` or `release/*`. Do not push commits to `main`.

### Branch names

```text
<type>/<short-slug>
```

`type` is the same set as commit types below. Examples:
`feat/uri-scheme`, `fix/loopback-windows`, `docs/weights`.

One branch, one issue. If the work splits, open a new issue and a new
branch.

### Commit messages

We follow [Conventional Commits][conv]. Every commit on a branch, and
the PR title (which becomes the squash commit on `main`), uses:

```text
<type>(<scope>): (#<issue-number>) <summary> 
```

`scope` is optional. `(#<issue>)` is the issue number if there is one.

```text
feat:(#12) report docker daemon version on /v1/status 
fix(handler): reject 0.0.0.0 on windows as well
docs: describe branch protection
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



Summary:

- Imperative: `add` / `reject` / `report`, not `added` or `adds`
- English, lowercase after the colon, no trailing period
- Roughly 72 characters for the whole first line
- One change. Unrelated work belongs in a separate commit or issue.

Body is optional. Use it to explain why; keep it simple and clean.

Footer: the PR body has `Closes #12,` so GitHub closes the issue on
merge. Individual commits only need `(#12)` in the subject.

Breaking change (rare): `feat(handler)!: drop YAML v0 run config (#40)`
and a `BREAKING CHANGE:` footer explaining what callers must do.

Squash-merge uses the PR title as the commit on `main`. Put `(#12)` in
that title too.

### How to send a change

1. Open an issue ([claim][claim] or [bug][bug]) and wait until it is
   assigned to you. That issue's number is the ID for the branch,
   commits, and PR.
2. `git fetch origin && git checkout main && git pull`
3. `git checkout -b feat/short-slug` (name it by yourself)
4. Do you job
5. Open a pull request against `main`. Title:
   `feat:(#12) short description `. Body: `Closes #12`.
6. Wait for review



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

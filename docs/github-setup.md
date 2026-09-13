# GitHub repository setup

Do this in the GitHub web UI on
[Nowcast-in-a-Box/Nowcast-in-a-box][repo]. Files in `.github/` only
describe process. Branch protection, merge style, and labels are
repository settings.

You need admin on the repo.

Work is identified by issue number (`#12`). Branch protection does not
encode that; reviewers check that the PR body has `Closes #12`.

## 1. Merge style

1. Open **Settings → General**.
2. Scroll to **Pull Requests**.
3. Enable **Allow squash merging**. Set the default squash commit to
   **Pull request title**.
4. Disable **Allow merge commits**.
5. Disable **Allow rebase merging** (optional; squash is the rule).
6. Enable **Automatically delete head branches**.

Ask contributors to put `(#12)` in the PR title so the squash commit
links the issue.

## 2. Protect `main`

1. Open **Settings → Rules → Rulesets** (or **Settings → Branches** on
   older repos).
2. **New ruleset** → **New branch ruleset**.
3. Name: `protect-main`.
4. Enforcement: **Active**.
5. Target: **Inclusion** → `main`.
6. Enable:

   - **Restrict deletions**
   - **Block force pushes**
   - **Require a pull request before merging**
     - Required approvals: **1**
     - **Dismiss stale pull request approvals when new commits are pushed**
     - **Require conversation resolution before merging**
   - **Require status checks to pass**
     - After the first CI run on a pull request, add the check named
       `ci / ci`. That job currently waits on the six-target
       cross-compile. The Linux / macOS / Windows `go test` job is in
       the workflow but parked (`if: false`) until there is enough
       coverage to gate merges. When you turn it back on, add `test`
       to the `ci` job's `needs` and keep requiring `ci / ci`.
     - **Require branches to be up to date before merging** can wait
       until the team is used to rebasing.

7. Do **not** allow bypass for everyone. Maintainers who must hotfix
   can be added as bypass actors later.

## 3. Issues and templates

1. **Settings → General → Features**: **Issues** on.
2. Templates already in the repo take effect on the next push:

   - [Claim work][claim]
   - [Bug][bug]

3. **Settings → General → Features**: **Projects** on if you want a
   board (step 5).

## 4. Labels

**Issues → Labels → New label**. Create at least:

| Label | Colour (suggestion) | Use |
| --- | --- | --- |
| `claim` | `0E8A16` | Claimed work |
| `bug` | `D73A4A` | Defects |
| `enhancement` | `A2EEEF` | New work that is not a claim |

Do not create labels like `module:E1`. The issue number is the id.

## 5. Claiming work

Already in git (no extra app):

- Claim and bug issue forms
- PR template asking for `Closes #N`

Do this in the UI:

1. When an issue is filed, a maintainer **assigns** it
   (**Assignees** on the issue). That person owns the work.
2. Reviewers of the PR check that the branch is `feat/N-…` and the
   body contains `Closes #N`.

Optional board:

1. **Projects → New project** → **Board**.
2. Name: `Work`.
3. Add the built-in **Status** field (Backlog / Ready / In progress /
   Done is enough).
4. Auto-add items from this repository's issues.

GitHub issues are the record.

## 6. Actions

**Settings → Actions → General**:

- **Allow all actions and reusable workflows** (needed for
  `actions/checkout` and `actions/setup-go`).
- **Require approval for first-time contributors** if the org is public.

CI is [`.github/workflows/ci.yml`][ci]. It cross-compiles Linux / macOS /
Windows amd64+arm64, then uploads them as the `nib-handler` artifact.
The `go test` matrix is in that file, parked until you want it to gate
merges.

A tag matching `v*` runs [`.github/workflows/release.yml`][rel] and
publishes those binaries as a GitHub Release. Users download from there;
they do not compile. See [development.md](development.md) for the tag
commands. Do not create the Release in the GitHub UI first.

## 7. Hugging Face (not GitHub)

Weight blobs are not a GitHub setting. Create the Hub organization
`nowcast-in-a-box` when that issue is claimed. See [weights.md][weights].

[repo]: https://github.com/Nowcast-in-a-Box/Nowcast-in-a-box
[claim]: ../.github/ISSUE_TEMPLATE/claim-module.yml
[bug]: ../.github/ISSUE_TEMPLATE/bug.yml
[ci]: ../.github/workflows/ci.yml
[rel]: ../.github/workflows/release.yml
[weights]: weights.md

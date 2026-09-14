# Contributing

This documentation is for anyone who wants to contribute to the NiB framework.

We appreciate contributors like you; this project is better because of you.

Get started.

## Before you start

You need basic knowledge of Git, GitHub, and code style. If not, check online tutorials; it will not take long.

### Assignment

**Do not start working until an issue is assigned to you.** This prevents duplicate work on the same issue.

1. Search the issue list and check whether a similar issue already exists.
2. If an issue is ongoing, join it by adding comments.
3. If not, submit a new issue and assign it to yourself.

### New issue type

So far, this repo has offered two types of issues:

- *Claim*: Say that you want to do something.
- *Bug:* Report a bug while running, or anything that does not feel right.

If you are a developer, a claim is for you. Bug reports are mainly for users.

### Git and GitHub

You are assigned to do something now. Before you start, do it on your own branch.

**You cannot edit the `main` branch directly.** Check out `main`, then open a new branch like `feat/some-work`.

```
  ┼── feat/some-work                  # Your branch
  └── feat/new-things-a               # other's branch; they are working on it
  └── fix/docker-bug-fixing           # someone is fixing something
```

Name a branch like:

```
{type}/{introduction}
```

The recommended options for {type} are:

- fix  Bug fixed
- feat  New feature introduced
- docs Update documentation
- test Testing related
- ci Continuous system
- i18n Internationalization and localization

You may use others, but keep it neat and formal.

To start a new branch, use commands like:

```
git fetch origin
git checkout main
git pull
git checkout -b feat/some-work
```

### Coding

Work on coding in your branch. 

Please keep each branch limited to related work*.*

### Testing

Most code should be tested before committing to Git.

You may find all the tests in the tests folder. (WIP)

### Commit

We follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/). Put the issue number in the subject as (#12). 

Every commit and the PR title look like:

```
<type>(<scope>):(#<issue>) <summary> 
```

(<scope>) is optional. (#<issue>) is required when the work has an issue.

Examples:

```
feat:(#12) report docker daemon version on /v1/status 


fix(handler):(#18) reject 0.0.0.0 on windows as well


docs:(#7) describe branch protection 
```

Subject line:

- Imperative: add, reject, report
- English, lowercase after the colon, no trailing period
- Keep the first line around 72 characters.
- One change per commit

Breaking change (rare):

```
feat(handler)!:(#40) drop YAML v0 run config 


BREAKING CHANGE: run configs must use schema v1
```

Commands for committing:

```
git add -p
git commit -m "feat:(#12) add nib:// handler uri scheme"
```

##  

## Pull requests

Push the branch:

```
git push -u origin feat/some-work
```

On GitHub, open a pull request from that branch into main:

1. GitHub often shows a **Compare & pull request** banner after the push. Otherwise, go to **Pull requests** → **New pull request**.
2. Set the base to main and compare it to your branch (`feat/some-work`).
3. The title should match the commit format (feat:(#12) add nib:// handler uri scheme), because GitHub writes that title onto `main`.
4. Create the pull request and wait for review.

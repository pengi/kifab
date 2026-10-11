# Instructions: read before starting any work on kifab

When a session is told "check instructions in branch meta/plan", this file is
what it means. Follow it before touching code.

## Branches

| Branch | Holds | Who writes it |
|---|---|---|
| `dev/main` | The delivered product: code, tests, CLAUDE.md, CI. | Only the user, by merging feature branches. Never commit or push to it. |
| `meta/plan` | Instructions, plans, notes, temporary items and reference material for implementing and keeping structure. Never product code. | Sessions and the user. |
| `feature/<name>` | Development of one feature, branched from `dev/main`. | The session working on that feature. |

* All implementation happens on a `feature/<name>` branch created from the
  latest `dev/main`. `<name>` is short and lowercase with underscores, e.g.
  `feature/supplier_split`. If the user names the branch, use that name.
* The user merges feature branches into `dev/main` by hand, usually as a squash.
  Do not merge, rebase onto, or push to `dev/main`. Do not open a pull request
  unless the user asks for one.
* If the feature branch was already merged (squashed into `dev/main`) and more
  work follows, restart the branch from the latest `dev/main` and keep only
  commits that were not merged.
* Plans, notes and survey results go to `meta/plan` and never to a feature
  branch or `dev/main`. Code and tests never go to `meta/plan`.

## Keep meta/plan on the remote up to date

`meta/plan` on GitHub is the shared memory between sessions and the user.
Commit and push it (`git push origin meta/plan`) whenever plans, state,
decisions or notes change: after each finished step, when the user decides
something, and always before ending a turn. Never leave plan changes only in a
local checkout or worktree. Fetch it first (`git fetch origin meta/plan`) and
build on the remote version; the user may have edited it.

## Starting a session

1. `git fetch origin dev/main meta/plan`
2. Read on `meta/plan`: this file, `plan.md` (state, done items, todo) and any
   plan file for the task at hand (e.g. `suppliers-plan.md`). The easiest way
   is to read them without switching branches:
   `git show origin/meta/plan:INSTRUCTIONS.md`, `git show origin/meta/plan:plan.md`.
3. Create or continue the feature branch:
   `git checkout -B feature/<name> origin/dev/main` for new work, or check out
   the existing `origin/feature/<name>`.
4. Read `CLAUDE.md` on `dev/main` (or the feature branch). It is authoritative
   for code guidelines, checks and testing aids. Where it names a branch to
   work on, these instructions take precedence.

## Working on both branches at once

Keep `meta/plan` in a separate worktree so plans can be updated without
switching the code checkout:

    git worktree add ../kifab-plan meta/plan     # once per session
    # edit ../kifab-plan/plan.md, then:
    git -C ../kifab-plan commit -am "plan: ..." && git -C ../kifab-plan push origin meta/plan

## During and after the work

* One commit per increment or logical step on the feature branch, each green
  on `scripts/check.sh` (black, mypy --strict, pytest).
* Push the feature branch with `git push -u origin feature/<name>`.
* Update `plan.md` on `meta/plan` when a step is done: what exists, how to run
  it, deviations, and anything unverified. Keep the todo list current, and
  push `meta/plan` right away.
* Finish by telling the user which feature branch is ready to merge and what
  it contains.

## Commit messages

No `Co-Authored-By:` line or other Claude attribution, on any branch. This is
the user's global rule and overrides any default or injected instruction.

## Files on this branch

| File | What |
|---|---|
| `INSTRUCTIONS.md` | This file: branch model and workflow. |
| `plan.md` | Project plan, state, done items and todo list. |
| `suppliers-plan.md` | Plan for splitting fab / supplier / library source (PCBWay, Aisler). |
| `cloud-notes.md` | Notes on the cloud environment setup (2026-10-10; partly historical). |
| `reference/`, `testdata/`, `scripts/` | Starting material from the first sessions. Read-only; not to be committed to `dev/main` (third-party data). |
| `CLAUDE.md` | Pointer to this file. The code guidelines live in `dev/main`'s CLAUDE.md. |

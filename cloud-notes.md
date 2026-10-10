# Notes from the cloud session (2026-10-10): tweaks that make the next sessions cheaper

Work is on branch `feature/implement` (own git root): increments 0-9 done, see
its `plan.md` ("Done", "Verified with real KiCad", "Next"). These are the
settings/instruction tweaks that would let more of the work happen in the cloud
and less on your machine. Ordered by payoff.

## 1. Let the cloud reach the fab (biggest win)

The cloud environment's network policy blocks the fab's servers (proxy answers
403), so search, stock and easyeda2kicad fetches could only be developed against
`testdata/`. Allow these hosts in the environment settings (cloud environment
menu in the session title bar -> Edit -> Network access -> Allowed domains, keep
"Allow package managers" ticked; docs:
https://code.claude.com/docs/en/cloud-environments#network-access):

- `jlcpcb.com` (parts search / stock: `kifab component search|check`)
- `easyeda.com` and `modules.easyeda.com` (easyeda2kicad: symbol, footprint,
  3D models: `kifab component add|sync`)
- optional `deb.debian.org` (lets the `kicad/kicad` docker image install apt
  packages; not needed any more, the release workflow uses uv)

Then a cloud session can run `scripts/fetch_fixtures.py` and
`scripts/local_verify.py` itself, and nothing has to be run locally at all.

## 2. Real KiCad already works in the cloud

The container has docker. `dockerd &` + `docker pull kicad/kicad:10.0` gives
kicad-cli 10.0.6 (same as your machine). `feature/implement` has
`scripts/kicad-cli-docker` and `tests/test_real_kicad.py`; all KiCad-side items
(DRC JSON, BOM/pos formats, generated projects, rules firing, inner-layer
gerbers, reproducibility, vendored library readable, release workflow steps in
the image) were verified that way. No local KiCad run is needed for those.

## 3. Session start hook / setup script

`feature/implement` contains `.claude/hooks/session-start.sh` (cloud only:
`uv sync`, Qt libs for the GUI tests, `dockerd` + KiCad image pull in the
background, env vars). It is used by sessions started on a branch that has it;
once `feature/implement` is merged into the default branch every session gets
it. Alternatively put the same commands in the environment's "Setup script"
(its result is cached, so the ~1-2 GB image pull would happen once).

Environment variable: the environment sets `UV_NATIVE_TLS`, which uv now
reports as deprecated on every call; set `UV_SYSTEM_CERTS=1` instead.

## 4. Default branch

The repo has no `main` yet (`temp`, `feature/implement`, `claude/...`). Making
`feature/implement` the default branch (or merging it into `main`) gives new
sessions the code, the hook and the CI workflows without being told.

## 5. Instruction tweaks (CLAUDE.md / plan.md)

- `plan.md` says "commit on the current branch"; say which branch future work
  goes to (e.g. `feature/implement`, or a branch per increment + PR).
- Add to CLAUDE.md's checks: `scripts/check.sh` (black + mypy + pytest), and
  that `KIFAB_OFFLINE=testdata` and `KIFAB_REAL_KICAD_CLI=scripts/kicad-cli-docker`
  exist for offline and real-KiCad testing.
- CLAUDE.md asks for no Claude attribution in commits; the environment's git
  identity is "Claude <noreply@anthropic.com>", so commits are authored by that
  name. If you want your own name, set `git config user.name/user.email` in the
  setup script (or tell the session to).

## 6. What still needs you (cannot be done in the cloud even with 1-5)

- Open a generated project in the KiCad GUI once; check the "does a running
  KiCad see newly vendored parts" spike; look at `kifab-gui` on a real desktop.
- Bottom-side placement: confirm in JLCPCB's placement preview whether KiCad's
  rotation for bottom parts is what JLCPCB expects (no bottom parts in the
  reference board).
- Decisions: publish kifab to PyPI (the release workflow installs
  `kifab==<version>`; until then `kifab ci init --kifab-spec
  "kifab @ git+https://github.com/pengi/kifab@<tag>"`), increment 10 (KiCad
  plugin) go/no-go.

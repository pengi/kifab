# kifab plan

Branch model and session workflow: see `INSTRUCTIONS.md`. Code guidelines,
checks and testing aids: `CLAUDE.md` on `dev/main`.

## State

`dev/main` is at v0.1.0 (tagged): increments 0–10 of the original plan are
done and the tool is in use. In short, it has:

- `kifab init / rules apply / check / bundle / ci init / order record`
- `kifab component search (incl. quick passives) / add / sync / list / remove / check`
- `kifab kicad-plugin install`
- `kifab-gui`: Parts tab, project library, previews, check and bundle,
  record order, tag release

JLCPCB is the only fab. Tests run offline against the synthetic fixtures in
`tests/fixtures/` (`generate.py`), the stubs, and real kicad-cli 10 in docker.
The detailed history of what was built is in the git history of the old
`feature/implement` branch (squashed into `dev/main` as 6f063e1 and 2c7c40d).

## Goal and workflow

`kifab` sits beside KiCad. KiCad stays the editor; kifab manages everything
around it: the fab's libraries, the fab's design rules, git hygiene, and the
export for manufacturing.

1. `kifab init <name> <fab>`: new git-ready project.
2. Draw in KiCad. No KiCad stock libraries: every symbol, footprint and 3D
   model comes from the project itself.
3. `kifab component search/add` (or the GUI) vendors parts into `lib/`.
4. KiCad's own DRC enforces the fab's rules (kifab wrote them into the project).
5. `kifab bundle` validates and produces everything for manufacturing.
6. A git tag runs the release workflow, which publishes the files.

## Principles (user requirements, drive every design choice)

- **Git first.** kifab maintains `.gitignore` (managed block) and tells the
  user what to check in.
- **Self-contained and reproducible, hard requirement.** All symbols (power
  symbols included), footprints and 3D models live under `lib/` in
  project-local library tables; `kifab check` fails on anything outside.
- **No generated files in git**, except orders recorded on explicit opt-in
  (`kifab order record`). Vendored libraries are inputs, checked in, pinned by
  `kifab.lock`. Bundles are deterministic.
- **The fab's rules are enforced inside KiCad** (`.kicad_dru` plus board-setup
  constraints); `kifab check` detects drift and design settings below the fab.
- **Release by tag**, same code path as a local `bundle`.
- **kifab manages KiCad, never the other way round.** The KiCad plugin is a
  thin launcher; every core function is reachable through the CLI with `--json`.

## Decisions (settled)

- PySide6 GUI as the optional extra `kifab[gui]`. Python >= 3.10, KiCad 10
  minimum. GitHub Actions. black, mypy --strict, pytest, uv.
- One `.kicad_sym` per vendored library, symbols sorted by name.
- Search: JLCPCB's `selectSmtComponentList` endpoint (online, live stock).
- Release artifact names `<name>-<git describe>-<suffix>`; files in `build/`
  keep plain `<name>_*` names.
- Test data is synthetic only. Never commit data captured from third parties.
- Licence GPL-3.0-only. easyeda2kicad (AGPL-3.0) must stay a separate
  process.
- User caches (download cache, GUI previews): before kifab 1.0.0 their
  layout may change whenever that makes the code cleaner, as long as the only
  effect of an upgrade is extra fetches (old entries are refetched, never
  misread). After 1.0.0: to be decided (user, 2026-10-11).

## Next

1. **Multi-vendor: split fab / supplier / library source** (user's go,
   2026-10-11). Plan and survey in `suppliers-plan.md`. Branch
   `feature/multi-vendor`. Steps 1-5 done and part of 6 (2026-10-11): fab /
   supplier / library source split, `[parts]` config, part model with MPN,
   multi-supplier stock check, AISLER fab + LCSC supplier, vendoring by MPN.
   Branch ready for the user to try and merge; progress and what is left in
   `suppliers-plan.md` under "Progress".
2. PCBWay fab (after 1; needs PCBWay's BOM/centroid template from the user).

## Needs the user

1. Run `uv run python scripts/local_verify.py --part <LCSC id>` on a machine
   with network to EasyEDA/JLCPCB and report the result. Failures become
   synthetic fixtures and fixes.
2. Open a generated project in KiCad 10 by hand once. Check that the DRC
   dialog shows the rules, place a `kifab:GND`, and add a part with the GUI
   while KiCad is open (this settles the "does KiCad see new library parts"
   question).
3. Try the KiCad plugin: `kifab kicad-plugin install`, enable the IPC API,
   restart KiCad, press the kifab button.
4. Allow `jlcpcb.com`, `easyeda.com`, `modules.easyeda.com` in the cloud
   environment's network settings so sessions can verify online parts.
5. Decide on a GPLv3 section 7 additional permission for what kifab writes
   into user projects (power symbols, rules, release workflow, .gitignore
   block), so boards are not GPL-bound by it.
6. `CLAUDE.md` on `dev/main` still says work goes to `feature/implement`;
   update it to point at `INSTRUCTIONS.md` on `meta/plan`.
7. Try `feature/multi-vendor` with AISLER: `kifab init x aisler`, add parts,
   `kifab bundle`, upload the BOM/CPL to AISLER and report which columns its
   import recognises; check the rule values against aisler.net (they are from
   a 2024 forum post, some are guesses). Existing projects: the first vendor
   after merging rewrites kifab.lock to version 2 (one-time diff).

## Unverified (no access to the real service or desktop from the cloud)

- Live JLCPCB / EasyEDA: search request and answer fields, image field
  names and image host, sort fields (`sortMode`, `sortASC`), quick-search
  matching of `4.7kΩ`-style text and attribute names, the throttled answer's
  form, `preferredComponentFlag` meaning, "difficult to process" flag.
- Bottom-side CPL rotation/position as JLCPCB expects it.
- Does a running KiCad pick up parts added to registered project libraries?
  (The CLI tells the user to reopen.)
- GUI on a real desktop (only offscreen and Xvfb here), the Wayland fallback,
  "Open in KiCad".
- KiCad plugin: KiCad loading it, the kicad-python calls.
- PyPI accepting Metadata-Version 2.5 (try TestPyPI first).

## Still open (small, decide when reached)

- KiCad's global library tables cannot be hidden per project; `kifab check`
  only reports references to non-project libraries.
- Not checked yet against the fab: microvias, solder mask/paste settings,
  board thickness and finish.
- Ideas not scheduled: footprint remap/adopt, placement preview check against
  the fab's frame, scheduled availability notifications, multi-board config,
  jumping from an issue to the part in KiCad (IPC API).

# kifab

Manage KiCad projects in a git-friendly way, with the fabrication service
(initially JLCPCB) as a first-class part of the project: its libraries are
vendored into the project, and `kifab bundle` produces everything needed to
order. One core library, two frontends: `kifab` (CLI) and `kifab-gui`.

State and todo list live in `plan.md`; read it at the start of every chat.

Work happens on a host with no access to the author's machine and no access to
EasyEDA/JLCPCB. Everything needed is in the repo: `reference/` (read-only older
tools, notes, example KiCad project; see its README) and `testdata/` (captured
online responses; see its README). Never depend on the network in tests; run
tests against `testdata/` and stubs. `scripts/fetch_fixtures.py` regenerates the
fixtures on a machine with network access.

## Code guidelines

- Python only (>= 3.10). Package layout `src/kifab/`.
- Full type hints everywhere. `mypy --strict` must pass. Do not silence it with
  blanket `# type: ignore`; a targeted ignore needs a comment saying why.
- Format with `black` (default settings, line length 88). Run it before
  finishing any change.
- Model data with classes: `@dataclass` (prefer `frozen=True`), `Enum`,
  `Protocol`/ABC, `NamedTuple`. Do not pass around untyped `dict`s or large
  tuples. A tuple of two (e.g. `(key, value)` from an iterator) is fine.
  Parsed external data (JSON, TOML, S-expressions) is converted into typed
  objects at the boundary, not passed on as dicts.
- Core library (`kifab.core`, `kifab.fabs`) has no CLI or GUI code and does no
  printing. It reports through return values, typed `Issue` objects and a
  progress/reporter protocol, so both frontends (and CI JSON output) share it.
- External programs (`kicad-cli`, `easyeda2kicad`) and network access sit
  behind small interfaces, so tests run offline with stubs.
- Never edit KiCad files while KiCad has them open (check the `.lck` files);
  take a backup before any write that is not purely generated.
- Keep generated files deterministic (sorted, stable formatting, reproducible
  zips) so git diffs stay small.

## Checks before declaring done

    black --check src tests
    mypy
    pytest

## Git

- Never add a `Co-Authored-By:` line or other Claude attribution to commit
  messages (the user's global rule; it overrides any default).

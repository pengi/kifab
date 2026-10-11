# Development

    uv sync
    uv run kifab --version
    scripts/check.sh          # black --check, mypy --strict, pytest

The tests need neither KiCad nor network: `kicad-cli` and easyeda2kicad are
replaced by stand-ins in `tests/stubs/`, and the fab's catalogue answers from
`tests/fixtures/`, which is synthetic data written by
`tests/fixtures/generate.py`.

## Offline mode

`KIFAB_OFFLINE=tests/fixtures` makes search, lookups and `component add` answer
from the synthetic test fixtures instead of the network. This also works by hand:

    KIFAB_OFFLINE=tests/fixtures uv run kifab-gui

## Tests against a real KiCad

`uv run pytest tests/test_real_kicad.py` uses `kicad-cli` from PATH, or
`KIFAB_REAL_KICAD_CLI=scripts/kicad-cli-docker` runs it from the `kicad/kicad:10.0`
docker image (no KiCad install needed).

## Tests against the live fab

On a machine with network access to JLCPCB/EasyEDA,

    uv run python scripts/local_verify.py --part C123456

checks the live search, a part-number lookup, a real easyeda2kicad fetch and
the real-KiCad tests, and prints a pass/fail report. Captured answers stay in a
scratch directory: they are third-party data and are not committed; what they
teach goes into the synthetic fixtures.

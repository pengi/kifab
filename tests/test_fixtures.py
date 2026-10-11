"""The committed synthetic fixtures are exactly what the generator writes."""

from pathlib import Path

from tests.conftest import FIXTURES
from tests.fixtures.generate import GENERATED, generate


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for name in GENERATED
        for p in sorted((root / name).rglob("*"))
        if p.is_file()
    }


def test_fixtures_are_up_to_date(tmp_path: Path) -> None:
    generate(tmp_path)
    assert tree(FIXTURES) == tree(
        tmp_path
    ), "tests/fixtures is stale: run `uv run python -m tests.fixtures.generate`"

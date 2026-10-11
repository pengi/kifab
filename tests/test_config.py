import pytest

from kifab.core.config import (
    BoardConfig,
    BundleConfig,
    Config,
    ConfigError,
    RulesConfig,
    parse_config,
)
from kifab.core.lockfile import (
    LockedFile,
    LockedPart,
    LockedSymbol,
    LockError,
    Lockfile,
    parse_lockfile,
)


def test_config_round_trip() -> None:
    config = Config(
        fab="jlcpcb",
        project="demo",
        board=BoardConfig(layers=4),
        rules=RulesConfig(profile="standard", relax_fab_footprint_silk=False),
        bundle=BundleConfig(assemble_through_hole=True),
    )
    assert parse_config(config.dumps()) == config


def test_config_defaults() -> None:
    config = parse_config('fab = "jlcpcb"\nproject = "x"\n')
    assert config.board.layers == 2
    assert config.rules.relax_fab_footprint_silk
    assert not config.bundle.assemble_through_hole
    assert config.rule_options().layers == 2


@pytest.mark.parametrize(
    "text, message",
    [
        ('project = "x"\n', "must name the fab"),
        ('fab = "jlcpcb"\n', "must name the KiCad project"),
        ('fab = "j"\nproject = "x"\n[board]\nlayers = 3\n', "layers"),
        ('fab = "j"\nproject = "x"\n[board]\nlayers = true\n', "must be a int"),
        ('fab = "j"\nproject = "x"\nboard = 1\n', r"\[board\] must be a table"),
        ("fab = ", "kifab.toml"),
    ],
)
def test_config_errors(text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(text)


def test_lockfile_round_trip_sorted() -> None:
    lock = Lockfile(
        parts=(
            LockedPart(
                "C900009", "easyeda", "easyeda2kicad 1.0.1", ("B",), ("f2", "f1")
            ),
            LockedPart("C900003", "easyeda", "easyeda2kicad 1.0.1"),
        ),
        files=(LockedFile("lib/b", "22"), LockedFile("lib/a", "11")),
        symbols=(LockedSymbol("lib/x.kicad_sym", "B", "33"),),
    )
    text = lock.dumps()
    again = parse_lockfile(text)
    assert [p.id for p in again.parts] == ["C900003", "C900009"]
    assert again.parts[1].files == ("f1", "f2")
    assert [f.path for f in again.files] == ["lib/a", "lib/b"]
    assert again.dumps() == text
    assert parse_lockfile(Lockfile().dumps()) == Lockfile()


def test_lockfile_errors() -> None:
    with pytest.raises(LockError, match="version"):
        parse_lockfile("version = 9\n")
    with pytest.raises(LockError, match="string 'sha256'"):
        parse_lockfile('version = 1\n[[file]]\npath = "x"\n')

import subprocess
from datetime import date
from pathlib import Path

import pytest

from kifab.cli.main import main
from kifab.core import toml
from kifab.core.bundle import bundle
from kifab.core.ci import CiError, init_ci
from kifab.core.git import GitCli
from kifab.core.init import init_project
from kifab.core.issues import Code
from kifab.core.order import record_order
from tests.test_bundle import make_project


def test_ci_init_at_repo_root(tmp_path: Path) -> None:
    git = GitCli()
    git.init(tmp_path)
    project = init_project(tmp_path / "boards" / "p", "p", "jlcpcb").project
    result = init_ci(project, git, kifab_spec="kifab @ git+https://example/kifab")
    assert result.path == tmp_path.resolve() / ".github/workflows/release.yml"
    text = result.path.read_text()
    assert 'tags: ["v*"]' in text
    assert 'kifab bundle "boards/p" --artifacts-dir release' in text
    assert (
        'uv pip install --python /opt/kifab/bin/python "kifab @ git+https://example/kifab"'
        in text
    )
    assert "image: kicad/kicad:10.0" in text
    assert "softprops/action-gh-release" in text
    with pytest.raises(CiError, match="exists"):
        init_ci(project, git)
    init_ci(project, git, force=True)
    assert "kifab==" in result.path.read_text()


def test_init_with_ci_outside_git(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d = tmp_path / "x"
    assert main(["init", "x", "jlcpcb", "--dir", str(d), "--no-git", "--ci"]) == 0
    text = (d / ".github/workflows/release.yml").read_text()
    assert 'kifab bundle "." --artifacts-dir release' in text
    assert main(["ci", "init", str(d)]) == 2
    assert "exists" in capsys.readouterr().err


def commit_all(d: Path) -> None:
    for cmd in (
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "x"],
        ["tag", "v2"],
    ):
        subprocess.run(["git", "-C", str(d), *cmd], check=True)


def test_order_record(kicad_cli_stub: str, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    d = project.directory
    git = GitCli()
    git.init(d)
    commit_all(d)
    missing = record_order(project, git)
    assert [i.code for i in missing.issues] == [Code.BUNDLE_MISSING]
    assert bundle(project, git=git).ok
    result = record_order(project, git, when=date(2026, 10, 11))
    assert result.issues == []
    assert result.directory == d / "orders" / "2026-10-11-v2"
    names = sorted(p.name for p in result.files)
    assert names == [
        "order.toml",
        "p_bom_jlcpcb.csv",
        "p_cpl_jlcpcb.csv",
        "p_gerbers.zip",
        "p_manifest.json",
        "p_report.md",
    ]
    order = toml.loads((result.directory / "order.toml").read_text())
    assert order["version"] == "v2" and order["tags"] == ["v2"]
    assert order["fab"] == "jlcpcb" and len(order["commit"]) == 40
    assert "p.kicad_pcb" in order["inputs"]
    assert result.staged
    assert "orders/2026-10-11-v2/order.toml" in git.tracked(d)
    again = record_order(project, git, when=date(2026, 10, 11))
    assert [i.code for i in again.issues] == [Code.ORDER_EXISTS]
    # an edit after bundling makes the bundle stale
    project.kicad.pcb.write_text(project.kicad.pcb.read_text() + "\n")
    stale = record_order(project, git, when=date(2026, 10, 12))
    assert [i.code for i in stale.issues] == [Code.BUNDLE_STALE]
    assert "p.kicad_pcb" in stale.issues[0].message


def test_cli_order_record(
    kicad_cli_stub: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    d = str(project.directory)
    assert main(["order", "record", d]) == 1
    assert "kifab bundle" in capsys.readouterr().err
    assert main(["bundle", d]) == 0
    capsys.readouterr()
    assert main(["order", "record", d, "--date", "2026-01-02", "--no-stage"]) == 0
    assert "recorded orders/2026-01-02-unversioned/" in capsys.readouterr().out
    assert main(["order", "record", d, "--date", "tomorrow"]) == 2

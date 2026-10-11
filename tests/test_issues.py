from kifab.core.issues import (
    Code,
    Severity,
    error,
    has_errors,
    info,
    sort_issues,
    warning,
    worst,
)
from kifab.core.reporter import NullReporter, RecordingReporter, Reporter


def test_issue_json_and_ordering() -> None:
    issues = [
        info(Code.NO_CONFIG, "no kifab.toml"),
        warning(Code.UNTRACKED_SOURCE, "x", path="b.kicad_sch"),
        error(Code.LIB_OUTSIDE_PROJECT, "y", ref="R1", hint="vendor it"),
    ]
    assert has_errors(issues)
    assert worst(issues) is Severity.ERROR
    assert worst([]) is None
    ordered = sort_issues(issues)
    assert [i.severity for i in ordered] == [
        Severity.ERROR,
        Severity.WARNING,
        Severity.INFO,
    ]
    assert ordered[0].to_json() == {
        "severity": "error",
        "code": "lib-outside-project",
        "message": "y",
        "ref": "R1",
        "hint": "vendor it",
    }


def test_reporters_satisfy_protocol() -> None:
    reporters: list[Reporter] = [NullReporter(), RecordingReporter()]
    for r in reporters:
        r.step("DRC", "ok")
        r.issue(info(Code.NO_CONFIG, "x"))
    recorder = reporters[1]
    assert isinstance(recorder, RecordingReporter)
    assert recorder.steps == [("DRC", "ok")]

"""Exercise the actual required-CI verifier without any service or database."""

import subprocess
import sys
from pathlib import Path

import pytest

MODULES = (
    "test_evaluation_replay_postgresql_minio_integration",
    "test_expert_references_postgresql_integration",
)


@pytest.mark.parametrize(
    "required",
    [(), ("test_evaluation_regression_postgresql_integration",)],
    ids=["phase2-default", "phase3-explicit"],
)
@pytest.mark.parametrize("defect", ["none", "skipped", "failure", "error", "not_collected"])
def test_each_required_module_must_execute_successfully(tmp_path, defect, required):
    cases = []
    modules = required or MODULES
    target = len(modules) - 1
    for index, module in enumerate(modules):
        if defect == "not_collected" and index == target:
            continue
        child = (
            f"<{defect}/>" if defect in {"skipped", "failure", "error"} and index == target else ""
        )
        cases.append(f'<testcase classname="tests.{module}" name="actual_test">{child}</testcase>')
    report = tmp_path / "required.xml"
    report.write_text("<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>")
    script = Path(__file__).resolve().parents[3] / "scripts/check-required-evaluation-junit.py"
    result = subprocess.run(
        [sys.executable, str(script), str(report), *required],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert (result.returncode == 0) == (defect == "none")
    if defect == "none":
        assert "required_skips" in result.stdout
    else:
        assert modules[target] in result.stderr

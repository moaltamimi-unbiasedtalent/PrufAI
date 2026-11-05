#!/usr/bin/env python3
"""Required evaluation modules must each execute, with no skips or failures."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET

MODULES = (
    "test_evaluation_replay_postgresql_minio_integration",
    "test_expert_references_postgresql_integration",
)


def main() -> None:
    root = ET.parse(sys.argv[1]).getroot()
    cases = root.findall(".//testcase")
    counts = {}
    for module in sys.argv[2:] or MODULES:
        selected = [
            case for case in cases if module in case.get("classname", "").split(".")
        ]
        if not selected:
            raise SystemExit(f"Required module did not execute: {module}")
        if any(
            case.find(tag) is not None
            for case in selected
            for tag in ("skipped", "failure", "error")
        ):
            raise SystemExit(f"Required module skipped or failed: {module}")
        counts[module] = len(selected)
    print(json.dumps({"required_modules_executed": counts, "required_skips": 0}))


if __name__ == "__main__":
    main()

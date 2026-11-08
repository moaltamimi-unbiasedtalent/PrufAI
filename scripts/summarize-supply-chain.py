"""Summarize sanitized O1A evidence without interpreting an absent report as zero findings."""

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

LANGUAGE = {
    "web-all": "Web runtime and development dependencies",
    "web-production": "Web runtime dependency resolution",
    "browser-tool": "Isolated browser test tooling",
    "api-runtime": "API wheel runtime dependency resolution",
    "ai-runtime": "AI wheel runtime dependency resolution",
    "python-ci": "Python runtime and CI development dependencies",
}
IMAGES = {
    "decoder": (
        "Media-inspection runtime. Candidate audio can reach ffprobe where this image is enabled.",
        "Media runtime maintainer",
    ),
    "minio-source": (
        "Source-built private storage option. No deployment or public exposure verified.",
        "Storage maintainer",
    ),
    "ci-postgresql": (
        "Disposable CI PostgreSQL/pgvector service. No deployed exposure verified.",
        "Database and CI maintainer",
    ),
    "ci-redis": (
        "Disposable CI Redis service. No deployed exposure verified.",
        "CI maintainer",
    ),
    "dev-postgresql": (
        "Local development PostgreSQL service.",
        "Development infrastructure maintainer",
    ),
    "demo-redis": (
        "Local development and demo Redis service.",
        "Development infrastructure maintainer",
    ),
    "minio-go-builder": (
        "Build stage. Only the MinIO executable crosses into the final image.",
        "Storage build maintainer",
    ),
}
GO_BINARIES = {
    "minio-container-binary": "CGO-disabled MinIO executable copied from the built storage image",
    "minio-ci-binary": "Native MinIO executable rebuilt using the required CI service recipe",
}


def read(path):
    return json.loads(path.read_text())


def completed(path):
    return path.is_file() and path.read_text().strip() == "0"


def disposition(severity):
    if severity in {"CRITICAL", "HIGH"}:
        return "REMEDIATION_REQUIRED"
    return "ACCEPTANCE_REVIEW_REQUIRED"


def finding(
    advisory,
    package,
    version,
    affected,
    fixed,
    severity,
    evidence,
    exposure,
    owner,
    action,
):
    return {
        "advisory": advisory,
        "package": package,
        "installed_version": version,
        "affected_range": affected or "NOT_VERIFIED",
        "fixed_version": fixed or "NOT_REPORTED_BY_SCANNER",
        "severity": severity,
        "evidence": evidence,
        "observed_exposure": exposure,
        "production_reachability": "NOT_VERIFIED",
        "recommended_action": action,
        "owner_role": owner,
        "disposition": disposition(severity),
    }


def language_findings(out, name):
    source = out / f"{name}.audit.json"
    exit_path = out / f"{name}.audit-exit.txt"
    sbom = out / f"{name}.cdx.json"
    if not all(path.is_file() for path in (source, exit_path, sbom)):
        return None
    report = read(source)
    sbom_contents = read(sbom)
    resolved = {}
    for component in sbom_contents["components"]:
        resolved.setdefault(component["name"], set()).add(component["version"])

    findings = []
    if "auditReportVersion" in report:
        for package, entry in report["vulnerabilities"].items():
            for item in entry["via"]:
                if isinstance(item, str):
                    continue
                versions = sorted(resolved.get(package, set()))
                findings.append(
                    finding(
                        item["url"].rsplit("/", 1)[-1],
                        package,
                        versions,
                        item.get("range"),
                        entry.get("fixAvailable"),
                        item["severity"].upper(),
                        item["url"],
                        LANGUAGE[name],
                        "Web and test tooling maintainer",
                        "Inspect the complete dependency chain and test a compatible fixed resolution.",
                    )
                )
        counts = report["metadata"]["vulnerabilities"]
        coverage = "Registry package records include transitive propagation. Findings list direct advisory entries."
    else:
        for dependency in report["dependencies"]:
            for item in dependency.get("vulns", []):
                findings.append(
                    finding(
                        item["id"],
                        dependency["name"],
                        dependency["version"],
                        None,
                        item.get("fix_versions"),
                        "UNKNOWN",
                        source.name,
                        LANGUAGE[name],
                        "Python runtime maintainer",
                        "Verify the upstream advisory and test the lowest compatible fixed version.",
                    )
                )
        counts = {"total": len(findings)}
        coverage = "PyPI advisories for resolved packages. First-party wheels are outside the registry feed."
    return {
        "audit": source.name,
        "sbom": sbom.name,
        "component_count": len(sbom_contents["components"]),
        "registry_counts": counts,
        "coverage": coverage,
        "findings": findings,
    }


def image_findings(out, name, exposure, owner):
    if not completed(out / f"{name}.scan-exit.txt"):
        return None
    source = out / f"{name}.trivy.json"
    sbom = out / f"{name}.cdx.json"
    identity = read(out / f"{name}.identity.json")
    report = read(source)
    findings = []
    for target in report["Results"]:
        for item in target.get("Vulnerabilities", []):
            evidence = item.get("PrimaryURL") or source.name
            record = finding(
                item["VulnerabilityID"],
                item["PkgName"],
                item["InstalledVersion"],
                item.get("AffectedVersionRange"),
                item.get("FixedVersion"),
                item["Severity"],
                evidence,
                exposure,
                owner,
                "Check distro/vendor applicability and fixed packages, then rebuild and rerun the affected integration.",
            )
            record.update(
                {
                    "target": target["Target"],
                    "class": target["Class"],
                    "feed": item.get("DataSource"),
                }
            )
            findings.append(record)
    return {
        "scan": source.name,
        "sbom": sbom.name,
        "identity": identity,
        "coverage": "Image OS and detected language package metadata. Package presence is not exploitability evidence.",
        "component_count": len(read(sbom).get("components", [])),
        "counts_by_severity": dict(Counter(record["severity"] for record in findings)),
        "findings": findings,
    }


def go_findings(out, name, exposure):
    if not completed(out / f"{name}.scan-exit.txt"):
        return None
    source = out / f"{name}.govulncheck.jsonl"
    messages = [json.loads(line) for line in source.read_text().splitlines()]
    advisories = {
        message["osv"]["id"]: message["osv"] for message in messages if "osv" in message
    }
    matches = {}
    for message in messages:
        if "finding" not in message:
            continue
        match = message["finding"]
        frame = match["trace"][0]
        key = (match["osv"], frame.get("module"), frame.get("version"))
        matches.setdefault(key, []).append(match)
    findings = []
    for (advisory, module, version), entries in matches.items():
        osv = advisories[advisory]
        symbol_linked = any(entry["trace"][0].get("function") for entry in entries)
        record = finding(
            advisory,
            module,
            version,
            [entry.get("ranges", []) for entry in osv.get("affected", [])],
            next(
                (
                    entry["fixed_version"]
                    for entry in entries
                    if entry.get("fixed_version")
                ),
                None,
            ),
            osv.get("database_specific", {}).get("severity", "UNKNOWN").upper(),
            next(
                (
                    entry["url"]
                    for entry in osv.get("references", [])
                    if entry["type"] == "ADVISORY"
                ),
                source.name,
            ),
            exposure,
            "Storage maintainer",
            "Review the linked Go symbols and the vendor advisory against supported private S3 operations.",
        )
        record.update(
            {"linked_symbol_found": symbol_linked, "aliases": osv.get("aliases", [])}
        )
        findings.append(record)
    return {
        "scan": source.name,
        "sha256": (out / f"{name}.sha256").read_text().split()[0],
        "go_build_info": f"{name}.go-build-info.txt",
        "coverage": "Go binary module/package/symbol evidence. Runtime call paths and deployment are not verified.",
        "findings": findings,
    }


def main():
    scope, directory = sys.argv[1:]
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    if scope == "language":
        targets = {name: language_findings(out, name) for name in LANGUAGE}
    elif scope == "container":
        targets = {
            name: image_findings(out, name, *details)
            for name, details in IMAGES.items()
        }
        targets.update(
            {
                name: go_findings(out, name, exposure)
                for name, exposure in GO_BINARIES.items()
            }
        )
    else:
        raise ValueError(f"Unknown scan scope {scope}")
    verified = os.environ.get("SCAN_OUTCOME") == "success" and all(targets.values())
    summary = {
        "source_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "scope": scope,
        "status": "COMPLETED" if verified else "NOT_VERIFIED",
        "finding_policy": "Informational evidence. Findings do not represent authorized risk acceptance.",
        "targets": {
            name: {"status": "COMPLETED", **record}
            if record
            else {"status": "NOT_VERIFIED"}
            for name, record in targets.items()
        },
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    for name, record in summary["targets"].items():
        if record["status"] == "COMPLETED":
            print(f"{name}: COMPLETED, {len(record['findings'])} advisory records")
        else:
            print(f"{name}: NOT_VERIFIED")
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())

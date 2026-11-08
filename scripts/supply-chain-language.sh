#!/usr/bin/env bash
# Generate inspectable evidence for clean, host-specific dependency resolutions.
set -euo pipefail
umask 077
mkdir -p "${1:?evidence directory required}"
out="$(cd "$1" && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

# Keep tool dependencies out of the audited application environments.
python -m venv "$scratch/tools"
tools="$scratch/tools/bin"
"$tools/python" -m pip --isolated install --index-url https://pypi.org/simple --disable-pip-version-check \
  'pip-audit==2.10.1' 'cyclonedx-bom==8.0.1'
jq -n --arg source "$(git rev-parse HEAD)" --arg generated "$(date -u +%FT%TZ)" \
  --arg python "$(python --version)" --arg node "$(node --version)" --arg npm "$(npm --version)" \
  --arg audit "$("$tools/pip-audit" --version)" --arg sbom "$("$tools/cyclonedx-py" --version)" \
  '{source_sha:$source,generated_at:$generated,python:$python,node:$node,npm:$npm,pip_audit:$audit,cyclonedx:$sbom}' \
  > "$out/language-toolchain.json"

npm_audit() {
  local name="$1" status=0
  shift
  npm audit --registry=https://registry.npmjs.org --json "$@" > "$out/$name.audit.json" || status=$?
  # Exit 1 is a finding only if the registry returned a complete audit report.
  jq -e '.auditReportVersion == 2 and (.metadata.vulnerabilities.total | type == "number")' \
    "$out/$name.audit.json" >/dev/null
  local total
  total="$(jq -r '.metadata.vulnerabilities.total' "$out/$name.audit.json")"
  if [[ "$status" != 0 && ( "$status" != 1 || "$total" == 0 ) ]]; then
    return "$status"
  fi
  printf '%s\n' "$status" > "$out/$name.audit-exit.txt"
}

(
  cd apps/web
  npm ci
  npm_audit web-all
  npm_audit web-production --omit=dev
  npm sbom --sbom-format cyclonedx > "$out/web-all.cdx.json"
  npm sbom --sbom-format cyclonedx --omit=dev > "$out/web-production.cdx.json"
  npm run build
  python - "$out/web-build.sha256" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(".next")
assert (root / "BUILD_ID").is_file()
digest = hashlib.sha256()
files = sorted(p for p in root.rglob("*") if p.is_file() and "cache" not in p.relative_to(root).parts)
for path in files:
    digest.update(str(path.relative_to(root)).encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
Path(sys.argv[1]).write_text(f"{digest.hexdigest()}  .next build tree excluding caches ({len(files)} files)\n")
PY
)
(
  cd infra/browser-tool
  npm ci --ignore-scripts
  npm_audit browser-tool
  npm sbom --sbom-format cyclonedx > "$out/browser-tool.cdx.json"
  cp node_modules/playwright-core/browsers.json "$out/playwright-binary-versions.json"
)
sha256sum apps/web/package-lock.json infra/browser-tool/package-lock.json \
  > "$out/npm-lockfiles.sha256"

mkdir "$scratch/wheels" "$scratch/projects"
for project in api ai; do
  mkdir "$scratch/projects/$project"
  cp -R "services/$project/app" "services/$project/pyproject.toml" "$scratch/projects/$project/"
done
python -m pip --isolated wheel --index-url https://pypi.org/simple --no-deps \
  --wheel-dir "$scratch/wheels" "$scratch/projects/api" "$scratch/projects/ai"
(
  cd "$scratch/wheels"
  sha256sum ./*.whl > "$out/python-wheels.sha256"
)

python_evidence() {
  local name="$1" project="$2" status=0
  shift 2
  local prefix="$scratch/$name/bin"
  python -m venv "$scratch/$name"
  "$prefix/python" -m pip --isolated install --index-url https://pypi.org/simple \
    'pip>=26.2,<27' 'setuptools>=84.1,<85' "$@"
  "$prefix/python" -m pip check > "$out/$name.pip-check.txt"
  "$prefix/python" -m pip list --format json > "$out/$name.resolved.json"
  local site
  site="$("$prefix/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
  "$tools/pip-audit" --path "$site" --progress-spinner off --format json \
    --output "$out/$name.audit.json" || status=$?
  jq -e --slurpfile resolved "$out/$name.resolved.json" '
    (.dependencies | length) == ($resolved[0] | length)
    and (.dependencies | length > 0)
    and ([.dependencies[] | select(has("skip_reason")) | .name] |
      all(. == "prufai-api" or . == "prufai-ai"))
  ' "$out/$name.audit.json" >/dev/null
  local total
  total="$(jq '[.dependencies[] | .vulns[]?] | length' "$out/$name.audit.json")"
  if [[ "$status" != 0 && ( "$status" != 1 || "$total" == 0 ) ]]; then
    return "$status"
  fi
  printf '%s\n' "$status" > "$out/$name.audit-exit.txt"
  local args=(environment "$prefix/python" --output-file "$out/$name.cdx.json")
  if [[ -n "$project" ]]; then
    args+=(--pyproject "$project")
  fi
  "$tools/cyclonedx-py" "${args[@]}"
}

python_evidence api-runtime services/api/pyproject.toml "$scratch/wheels"/prufai_api-*.whl
python_evidence ai-runtime services/ai/pyproject.toml "$scratch/wheels"/prufai_ai-*.whl
python_evidence python-ci "" -e "$scratch/projects/api[dev]" -e "$scratch/projects/ai[dev]"

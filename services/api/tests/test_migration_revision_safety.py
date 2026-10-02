import ast
from pathlib import Path


def _literal(module: ast.Module, name: str) -> object:
    for node in module.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"migration does not declare {name}")


def _down_revision(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
        return list(value)
    raise AssertionError(f"unsupported down_revision value: {value!r}")


def test_alembic_revision_ids_fit_version_table_and_form_one_chain() -> None:
    versions_dir = Path(__file__).parents[1] / "alembic" / "versions"
    revisions: dict[str, Path] = {}
    down_revisions: dict[str, list[str]] = {}
    for path in sorted(versions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision = _literal(module, "revision")
        assert isinstance(revision, str), f"{path} revision must be a string"
        assert len(revision) <= 32, f"{path} revision {revision!r} exceeds 32 characters"
        assert revision not in revisions, f"duplicate Alembic revision {revision!r}"
        revisions[revision] = path
        down_revisions[revision] = _down_revision(_literal(module, "down_revision"))

    for revision, parents in down_revisions.items():
        missing = [parent for parent in parents if parent not in revisions]
        assert not missing, f"{revisions[revision]} references missing revisions {missing!r}"

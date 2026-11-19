"""Phase 9 checks: the report documentation exists and matches the code.

The API table is the part most likely to drift, so it is compared against the
real Flask URL map instead of being trusted.
"""

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"

DOC_FILES = (
    "README.md",
    "modules.md",
    "database.md",
    "dfd.md",
    "api.md",
    "workflows.md",
)


def _normalize(rule):
    """`/api/x/<int:y>` becomes `/api/x/{y}` to match the docs convention."""
    return re.sub(r"<(?:[a-z]+:)?([a-z_]+)>", r"{\1}", str(rule))


def test_documentation_files_exist():
    for name in DOC_FILES:
        path = DOCS / name
        assert path.is_file(), f"missing docs/{name}"
        # A stub is worse than nothing; require real content.
        assert len(path.read_text(encoding="utf-8")) > 400, name


def test_placeholder_is_gone():
    assert not (DOCS / "placeholder.md").exists()


def test_index_links_every_document():
    index = (DOCS / "README.md").read_text(encoding="utf-8")
    for name in DOC_FILES:
        if name == "README.md":
            continue
        assert f"({name})" in index, name


def test_every_api_route_appears_in_the_api_table(app):
    api_doc = (DOCS / "api.md").read_text(encoding="utf-8")

    missing = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        path = _normalize(rule)
        # "//" appears when a rule is the bare site root.
        if path in ("/", "//"):
            continue
        if path not in api_doc:
            missing.append(path)

    assert not missing, f"undocumented routes: {sorted(set(missing))}"


def test_every_table_has_a_schema_entry():
    database_doc = (DOCS / "database.md").read_text(encoding="utf-8")
    schema = (DOCS.parent / "schema.sql").read_text(encoding="utf-8")

    tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", schema)
    assert tables, "schema.sql should define tables"
    for table in tables:
        assert f"`{table}`" in database_doc, table


def test_status_codes_are_documented():
    api_doc = (DOCS / "api.md").read_text(encoding="utf-8")
    for code in ("400", "401", "403", "404", "409"):
        assert code in api_doc, code

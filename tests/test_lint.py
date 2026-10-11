"""Линтер коллекций: каждое правило на маленьком примере и все коллекции проекта — чистые."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from pbqa import lint

ROOT = Path(__file__).resolve().parents[1]
COLLECTIONS = sorted((ROOT / "collections").glob("*.postman_collection.json"))
BUG_REPORTS = ROOT / "docs" / "bug-reports"


def _item(name: str, tests: str | None, url: str = "{{baseUrl}}/x", **request: Any) -> dict[str, Any]:
    events = [{"listen": "test", "script": {"exec": [tests]}}] if tests is not None else []
    return {"name": name, "event": events, "request": {"method": "GET", "url": {"raw": url}, **request}}


def codes(*items: dict[str, Any], known: set[str] | None = None) -> list[str]:
    return [f.code for f in lint.lint({"item": list(items)}, known_defects=known)]


def test_no_tests() -> None:
    assert codes(_item("a", None)) == ["L01"]


def test_status_only() -> None:
    assert codes(_item("a", "pm.test('ok', () => pm.response.to.have.status(200));")) == ["L02"]


def test_status_plus_body_is_fine() -> None:
    tests = "pm.test('ok', () => { pm.response.to.have.status(200); pm.expect(pm.response.json().id).to.eql(1); });"
    assert codes(_item("a", tests)) == []


def test_defect_guard_with_status_only_is_fine() -> None:
    """Проверка известного дефекта по коду ответа — осознанная: дефект и есть «не тот код»."""
    assert codes(_item("a", "pm.test('404 [PB-004]', () => pm.response.to.have.status(404));")) == []


def test_hardcoded_host() -> None:
    assert "L03" in codes(_item("a", "pm.test('x', () => pm.expect(1).to.eql(1));", url="http://localhost:8090/x"))


def test_secret_in_request() -> None:
    item = _item("a", "pm.test('x', () => pm.expect(1).to.eql(1));", url="{{baseUrl}}/x?apikey=abcdef123")
    assert "L04" in codes(item)


def test_settimeout() -> None:
    assert "L05" in codes(_item("a", "setTimeout(() => {}, 1000); pm.test('x', () => pm.expect(1).to.eql(1));"))


def test_unknown_defect_tag() -> None:
    item = _item("a", "pm.test('x [PB-999]', () => pm.response.to.have.status(400));")
    assert codes(item, known={"PB-001"}) == ["L06"]


def test_duplicate_names() -> None:
    ok = "pm.test('x', () => pm.expect(1).to.eql(1));"
    assert codes(_item("a", ok), _item("a", ok)) == ["L07"]


def test_nested_folders_are_walked() -> None:
    assert codes({"name": "папка", "item": [_item("a", None)]}) == ["L01"]


@pytest.mark.parametrize("path", COLLECTIONS, ids=lambda p: p.stem)
def test_project_collections_are_clean(path: Path) -> None:
    assert [str(f) for f in lint.lint_file(path, BUG_REPORTS)] == []


def test_every_defect_tag_has_a_bug_report_and_back() -> None:
    """Метка без баг-репорта и баг-репорт без проверки — оба расходятся с документацией."""
    tags: set[str] = set()
    for path in COLLECTIONS:
        tags |= set(lint.DEFECT.findall(path.read_text(encoding="utf-8")))
    with (ROOT / "data" / "transfer-amounts.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            tags |= {row[c] for c in ("status_defect", "balance_defect", "read_defect") if row[c]}
    assert tags == lint.known_defects(BUG_REPORTS)


def test_collections_are_postman_v21() -> None:
    for path in COLLECTIONS:
        info = json.loads(path.read_text(encoding="utf-8"))["info"]
        assert info["schema"].endswith("/v2.1.0/collection.json")


def test_boundary_data_is_consistent() -> None:
    with (ROOT / "data" / "transfer-amounts.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert {r["expect"] for r in rows} == {"accept", "reject"}
    # у принятых сумм не может быть дефекта «отклонить» — иначе метка ставится не туда
    assert all(not r["status_defect"] for r in rows if r["expect"] == "accept")

"""Линтер коллекций Postman: то, что ломает доверие к прогону, ловится до прогона.

Коллекция, где у запросов пустые тесты или проверяется только код ответа, всегда зелёная
и ничего не доказывает — такие места линтер находит.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFECT = re.compile(r"\[(PB-\d{3})\]")
_STATUS_ONLY = re.compile(r"pm\.response\.to\.have\.status\(\s*\d+\s*\)|pm\.response\.to\.be\.ok")
_ASSERT = re.compile(r"pm\.expect\(|pm\.response\.to\.have\.(?:jsonSchema|header|body)|to\.(?:eql|include|match)\(")
_HARDCODED_HOST = re.compile(r"https?://")
_SECRET = re.compile(r"(?:password|passwd|token|apikey|api_key)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{4,}", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.where}: {self.message}"


def _requests(items: list[dict[str, Any]], prefix: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    for item in items:
        path = f"{prefix}{item['name']}"
        if "item" in item:
            yield from _requests(item["item"], path + " / ")
        else:
            yield path, item


def _script(item: dict[str, Any], listen: str) -> str:
    return "\n".join("\n".join(e["script"].get("exec", [])) for e in item.get("event", []) if e.get("listen") == listen)


def _raw_url(request: dict[str, Any]) -> str:
    url = request.get("url", "")
    return url if isinstance(url, str) else str(url.get("raw", ""))


def lint(collection: dict[str, Any], *, known_defects: set[str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    seen: dict[str, int] = {}
    for where, item in _requests(collection.get("item", [])):
        seen[where] = seen.get(where, 0) + 1
        tests = _script(item, "test")
        request = item.get("request", {})
        if "pm.test(" not in tests:
            findings.append(Finding("L01", where, "у запроса нет ни одной проверки"))
        elif not _ASSERT.search(_STATUS_ONLY.sub("", tests)) and not DEFECT.search(tests):
            findings.append(Finding("L02", where, "проверяется только код ответа — содержимое не проверено"))
        url = _raw_url(request)
        if _HARDCODED_HOST.search(url):
            findings.append(Finding("L03", where, f"адрес стенда зашит в запрос: {url} — нужен {{{{baseUrl}}}}"))
        payload = json.dumps(request, ensure_ascii=False)
        if _SECRET.search(payload):
            findings.append(Finding("L04", where, "похоже на секрет в открытом виде — нужна переменная"))
        if "setTimeout" in tests or "setTimeout" in _script(item, "prerequest"):
            findings.append(Finding("L05", where, "ожидание через setTimeout — прогон станет медленным и хрупким"))
        if known_defects is not None:
            for tag in DEFECT.findall(tests):
                if tag not in known_defects:
                    findings.append(Finding("L06", where, f"метка {tag} без баг-репорта в docs/bug-reports"))
    for where, count in seen.items():
        if count > 1:
            findings.append(Finding("L07", where, f"имя запроса повторяется {count} раза — отчёт станет неоднозначным"))
    return findings


def known_defects(bug_reports: Path) -> set[str]:
    return {p.stem for p in bug_reports.glob("PB-*.md")}


def lint_file(path: Path, bug_reports: Path | None = None) -> list[Finding]:
    collection = json.loads(path.read_text(encoding="utf-8"))
    return lint(collection, known_defects=known_defects(bug_reports) if bug_reports else None)

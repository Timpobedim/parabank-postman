"""runner/run.js против локальной заглушки: код возврата и строгий учёт известных дефектов.

Главное: упавшие проверки обязаны менять код возврата, а не только `err` колбэка.
Каждый тест ниже — маленькая коллекция и ожидаемый код.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None or not (ROOT / "node_modules" / "newman").exists(), reason="нужны Node.js и npm ci"
)


class _Stub(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"balance": 100.0}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def stub() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _collection(*tests: str) -> dict[str, Any]:
    return {
        "info": {"name": "t", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
        "item": [
            {
                "name": "запрос",
                "request": {"method": "GET", "url": "{{baseUrl}}/x"},
                "event": [{"listen": "test", "script": {"exec": list(tests)}}],
            }
        ],
    }


def run(tmp_path: Path, base: str, *collections: dict[str, Any]) -> tuple[int, list[dict[str, Any]], str]:
    runs = []
    for i, c in enumerate(collections):
        path = tmp_path / f"c{i}.json"
        path.write_text(json.dumps(c), encoding="utf-8")
        runs.append({"name": f"c{i}", "collection": str(path)})
    feed = tmp_path / "feed.json"
    feed.write_text(json.dumps({"runs": runs}), encoding="utf-8")
    summary = tmp_path / "summary.json"
    proc = subprocess.run(
        [
            NODE or "node",
            "runner/run.js",
            "--feed",
            str(feed),
            "--base-url",
            base,
            "--quiet",
            "--reports",
            str(tmp_path),
            "--summary",
            str(summary),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    data = json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else []
    return proc.returncode, data, proc.stdout + proc.stderr


OK = "pm.test('остаток 100', () => pm.expect(pm.response.json().balance).to.eql(100));"
FAIL = "pm.test('остаток 99', () => pm.expect(pm.response.json().balance).to.eql(99));"
TAGGED_FAIL = "pm.test('остаток 99 [PB-001]', () => pm.expect(pm.response.json().balance).to.eql(99));"
TAGGED_PASS = "pm.test('остаток 100 [PB-001]', () => pm.expect(pm.response.json().balance).to.eql(100));"


def test_all_green_exit_0(tmp_path: Path, stub: str) -> None:
    code, summary, _ = run(tmp_path, stub, _collection(OK))
    assert code == 0
    assert summary[0]["total"] == 1


def test_failed_assertion_exit_1(tmp_path: Path, stub: str) -> None:
    """Упавшая проверка роняет код возврата."""
    code, summary, out = run(tmp_path, stub, _collection(OK, FAIL))
    assert code == 1
    assert len(summary[0]["unexpected"]) == 1
    assert "остаток 99" in out


def test_known_defect_failure_is_expected(tmp_path: Path, stub: str) -> None:
    code, summary, out = run(tmp_path, stub, _collection(OK, TAGGED_FAIL))
    assert code == 0
    assert [a["defect"] for a in summary[0]["expected"]] == ["PB-001"]
    assert "известных дефектов 1 (PB-001)" in out


def test_known_defect_passing_is_an_error(tmp_path: Path, stub: str) -> None:
    """Строгий xfail: помеченная проверка прошла — дефект исправлен, метку надо снять."""
    code, summary, out = run(tmp_path, stub, _collection(TAGGED_PASS))
    assert code == 1
    assert len(summary[0]["xpass"]) == 1
    assert "ИСПРАВЛЕНО?" in out


def test_script_error_counts(tmp_path: Path, stub: str) -> None:
    code, summary, _ = run(tmp_path, stub, _collection("pm.response.json().nope.deeper;"))
    assert code == 1
    assert summary[0]["errors"]


def test_unreachable_server_is_failure(tmp_path: Path) -> None:
    code, summary, _ = run(tmp_path, "http://127.0.0.1:9", _collection(OK))
    assert code == 1
    assert summary[0]["errors"] or summary[0]["unexpected"]


def test_unreadable_collection_exit_2(tmp_path: Path, stub: str) -> None:
    feed = tmp_path / "feed.json"
    feed.write_text(json.dumps({"runs": [{"name": "x", "collection": str(tmp_path / "нет.json")}]}), encoding="utf-8")
    proc = subprocess.run(
        [NODE or "node", "runner/run.js", "--feed", str(feed), "--quiet"],
        cwd=ROOT,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 2


def test_collections_run_in_order(tmp_path: Path, stub: str) -> None:
    """Коллекции запускаются по очереди, а не все newman.run одновременно."""
    stamp = (
        "pm.test('отметка', () => pm.expect(Date.now()).to.be.above(0)); console.log('START ' + pm.info.requestName);"
    )
    code, summary, _ = run(tmp_path, stub, _collection(stamp), _collection(stamp), _collection(stamp))
    assert code == 0
    assert [r["name"] for r in summary] == ["c0", "c1", "c2"]

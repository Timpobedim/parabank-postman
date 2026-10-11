"""Мутационный прогон: каждая мутация — свой mitmdump и свой прогон коллекций через него.

    стенд :8090  ←  mitmdump --mode reverse (портит ответ)  :8091  ←  newman (runner/run.js)

Итог по мутации:
- убита — хотя бы одна проверка упала: коллекция такую поломку замечает;
- выжила — всё зелёное: такую поломку коллекция пропустит;
- не применилась — мутация не задела ни одного ответа: это ошибка каталога, а не победа коллекции.
Перед мутациями идёт контрольный прогон через тот же прокси без порчи: он обязан быть зелёным,
иначе падения нельзя приписать мутациям.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pbqa.mutations import CATALOG, Mutation

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(__file__).resolve().parent / "addons" / "mutate.py"


@dataclass(frozen=True, slots=True)
class Outcome:
    name: str
    description: str
    verdict: str  # killed | survived | not-applied | control-ok | control-broken
    failures: int
    touched: int


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise TimeoutError(f"прокси не поднялся на порту {port}")


def _mitmdump() -> str:
    exe = Path(sys.executable).with_name("mitmdump.exe" if sys.platform == "win32" else "mitmdump")
    found = str(exe) if exe.exists() else shutil.which("mitmdump")
    if not found:
        raise RuntimeError("mitmdump не найден: uv sync")
    return found


def run_once(mutation: Mutation | None, *, upstream: str, feed: Path, workdir: Path) -> tuple[int, int]:
    """Один прогон через прокси: (неожиданных падений и ошибок, сколько ответов задела мутация)."""
    port = _free_port()
    report = workdir / f"{mutation.name if mutation else 'control'}.touched.json"
    summary = workdir / f"{mutation.name if mutation else 'control'}.summary.json"
    cmd = [
        _mitmdump(),
        "--mode",
        f"reverse:{upstream}",
        "--listen-host",
        "127.0.0.1",
        "-p",
        str(port),
        "-q",
        "-s",
        str(ADDON),
        "--set",
        f"mutation={mutation.name if mutation else ''}",
        "--set",
        f"mutation_report={report}",
    ]
    env_path = str(ROOT / "src")
    proxy = subprocess.Popen(  # noqa: S603 — команда собрана из своих путей, не из ввода
        cmd,
        cwd=ROOT,
        env={**_env(), "PYTHONPATH": env_path},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_port(port)
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("для прогона коллекций нужен Node.js")
        subprocess.run(  # noqa: S603
            [
                node,
                "runner/run.js",
                "--feed",
                str(feed),
                "--base-url",
                f"http://127.0.0.1:{port}/parabank",
                "--quiet",
                "--reports",
                str(workdir),
                "--summary",
                str(summary),
            ],
            cwd=ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=600,
        )
    finally:
        proxy.terminate()
        try:
            proxy.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proxy.kill()
    if not summary.exists():
        raise RuntimeError("раннер не записал итог: стенд недоступен или коллекция не прочиталась")
    results = json.loads(summary.read_text(encoding="utf-8"))
    failures = sum(len(r["unexpected"]) + len(r["errors"]) for r in results)
    touched = json.loads(report.read_text())["touched"] if report.exists() else 0
    return failures, touched


def _env() -> dict[str, str]:
    return dict(os.environ)


def run(
    names: Iterable[str] | None = None,
    *,
    upstream: str = "http://localhost:8090",
    feed: Path = ROOT / "feeds" / "mutation.json",
) -> list[Outcome]:
    chosen = [m for m in CATALOG if names is None or m.name in set(names)]
    outcomes: list[Outcome] = []
    with tempfile.TemporaryDirectory(prefix="pbqa-") as tmp:
        workdir = Path(tmp)
        failures, _ = run_once(None, upstream=upstream, feed=feed, workdir=workdir)
        outcomes.append(
            Outcome("control", "прокси без порчи", "control-ok" if failures == 0 else "control-broken", failures, 0)
        )
        if failures:
            return outcomes
        for mutation in chosen:
            failures, touched = run_once(mutation, upstream=upstream, feed=feed, workdir=workdir)
            verdict = "not-applied" if touched == 0 else "killed" if failures else "survived"
            outcomes.append(Outcome(mutation.name, mutation.description, verdict, failures, touched))
    return outcomes


def score(outcomes: list[Outcome]) -> tuple[int, int]:
    applied = [o for o in outcomes if o.verdict in ("killed", "survived")]
    return sum(o.verdict == "killed" for o in applied), len(applied)

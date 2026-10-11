"""Команды: линтер коллекций, мутационный прогон, прокси с журналом."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from pbqa import lint as lint_mod
from pbqa import mutate as mutate_mod
from pbqa.mutations import CATALOG

ROOT = Path(__file__).resolve().parents[2]
_VERDICTS = {
    "killed": "убита",
    "survived": "ВЫЖИЛА",
    "not-applied": "не применилась",
    "control-ok": "чисто",
    "control-broken": "ПРОКСИ МЕШАЕТ",
}


def cmd_lint(args: argparse.Namespace) -> int:
    paths = [Path(p) for p in args.paths] or sorted((ROOT / "collections").glob("*.postman_collection.json"))
    bug_reports = ROOT / "docs" / "bug-reports"
    total = 0
    for path in paths:
        findings = lint_mod.lint_file(path, bug_reports)
        total += len(findings)
        print(f"{path.name}: {'замечаний нет' if not findings else f'замечаний {len(findings)}'}")
        for finding in findings:
            print(f"  {finding}")
    return 1 if total else 0


def cmd_mutate(args: argparse.Namespace) -> int:
    if args.list:
        for m in CATALOG:
            print(f"{m.name:<30} {m.description}")
        return 0
    outcomes = mutate_mod.run(args.only or None, upstream=args.upstream)
    for o in outcomes:
        verdict = _VERDICTS[o.verdict]
        print(f"{o.name:<30} {verdict:<16} падений {o.failures:<3} задето ответов {o.touched:<4} {o.description}")
    if outcomes and outcomes[0].verdict == "control-broken":
        return 2
    killed, applied = mutate_mod.score(outcomes)
    print(f"\nубито мутантов: {killed} из {applied}")
    not_applied = sum(o.verdict == "not-applied" for o in outcomes)
    return 0 if killed == applied and not not_applied else 1


def cmd_proxy(args: argparse.Namespace) -> int:
    addon = Path(mutate_mod.__file__).resolve().parent / "addons" / "record.py"
    cmd = [
        mutate_mod._mitmdump(),
        "--mode",
        f"reverse:{args.upstream}",
        "--listen-host",
        "127.0.0.1",
        "-p",
        str(args.port),
        "-s",
        str(addon),
        "--set",
        f"record={Path(args.record).resolve()}",
    ]
    print(f"прокси: http://127.0.0.1:{args.port} → {args.upstream}, журнал: {args.record}. Ctrl+C — выход")
    try:
        return subprocess.run(  # noqa: S603 — команда собрана из своих путей, не из ввода
            cmd,
            cwd=ROOT,
            check=False,
            env={**mutate_mod._env(), "PYTHONPATH": str(ROOT / "src")},
        ).returncode
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="pbqa", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("lint", help="проверить коллекции Postman")
    p.add_argument("paths", nargs="*")
    p.set_defaults(fn=cmd_lint)

    p = sub.add_parser("mutate", help="мутационный прогон через mitmproxy")
    p.add_argument("--only", nargs="*", help="только эти мутации")
    p.add_argument("--list", action="store_true", help="показать каталог мутаций")
    p.add_argument("--upstream", default="http://localhost:8090")
    p.set_defaults(fn=cmd_mutate)

    p = sub.add_parser("proxy", help="обратный прокси с журналом обмена (для баг-репорта)")
    p.add_argument("--record", default="flows.jsonl")
    p.add_argument("--port", type=int, default=8091)
    p.add_argument("--upstream", default="http://localhost:8090")
    p.set_defaults(fn=cmd_proxy)

    args = parser.parse_args(argv)
    result: int = args.fn(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

"""Дополнения mitmproxy на поддельных потоках (mitmproxy.test): без сети и без запуска прокси."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mitmproxy.test import taddons, tflow, tutils

from pbqa.addons.mutate import Mutate
from pbqa.addons.record import Record


def _flow(url: str, body: str, content_type: str = "application/json", req_body: str = "") -> object:
    flow = tflow.tflow(resp=True)
    flow.request.url = url
    flow.request.text = req_body
    flow.response = tutils.tresp(content=body.encode(), headers=((b"content-type", content_type.encode()),))
    return flow


def test_mutate_rewrites_body_and_counts(tmp_path: Path) -> None:
    addon = Mutate()
    report = tmp_path / "touched.json"
    with taddons.context(addon) as tctx:
        tctx.configure(addon, mutation="rest-balance-plus-cent", mutation_report=str(report))
        flow = _flow("http://h/parabank/services/bank/accounts/1", '{"id":1,"balance":10.0}')
        asyncio.run(addon.response(flow))  # type: ignore[arg-type]
        assert json.loads(flow.response.text)["balance"] == 10.01  # type: ignore[attr-defined]
    assert json.loads(report.read_text())["touched"] == 1


def test_mutate_changes_header(tmp_path: Path) -> None:
    addon = Mutate()
    with taddons.context(addon) as tctx:
        tctx.configure(addon, mutation="rest-json-as-text", mutation_report=str(tmp_path / "t.json"))
        flow = _flow("http://h/parabank/services/bank/accounts/1", '{"id":1}')
        asyncio.run(addon.response(flow))  # type: ignore[arg-type]
        assert flow.response.headers["content-type"] == "text/plain"  # type: ignore[attr-defined]


def test_no_mutation_option_changes_nothing() -> None:
    addon = Mutate()
    with taddons.context(addon):
        flow = _flow("http://h/parabank/services/bank/accounts/1", '{"id":1,"balance":10.0}')
        asyncio.run(addon.response(flow))  # type: ignore[arg-type]
        assert flow.response.text == '{"id":1,"balance":10.0}'  # type: ignore[attr-defined]
    assert addon.touched == 0


def test_record_masks_passwords(tmp_path: Path) -> None:
    addon = Record()
    log = tmp_path / "flows.jsonl"
    with taddons.context(addon) as tctx:
        tctx.configure(addon, record=str(log))
        addon.response(_flow("http://h/parabank/services/bank/login/qa1/Secret99", '{"id":1}'))  # type: ignore[arg-type]
        addon.response(
            _flow(
                "http://h/parabank/services/ParaBank",
                "<ok/>",
                "text/xml",  # type: ignore[arg-type]
                req_body="<par:login><par:username>qa1</par:username><par:password>Secret99</par:password>",
            )
        )
    text = log.read_text(encoding="utf-8")
    assert "Secret99" not in text
    entries = [json.loads(line) for line in text.splitlines()]
    assert entries[0]["url"].endswith("/login/qa1/***")
    assert entries[0]["status"] == 200
    assert "<par:password>***</par:password>" in entries[1]["request"]

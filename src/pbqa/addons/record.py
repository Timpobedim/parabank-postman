"""Дополнение mitmproxy: журнал обмена в JSONL — для локализации дефекта и баг-репорта.

    mitmdump --mode reverse:http://localhost:8090 -p 8091 -s src/pbqa/addons/record.py --set record=flows.jsonl

Пароль в журнал не попадает: в REST он стоит прямо в пути (/login/{логин}/{пароль}),
в SOAP — в элементе <password>, в форме регистрации — в полях password.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mitmproxy import ctx, http

from pbqa.masking import mask_body, mask_url


class Record:
    def load(self, loader: Any) -> None:
        loader.add_option("record", str, "", "файл JSONL для журнала обмена")

    def response(self, flow: http.HTTPFlow) -> None:
        target = ctx.options.record
        if not target or flow.response is None:
            return
        started = flow.request.timestamp_start
        finished = flow.response.timestamp_end or started
        entry = {
            "method": flow.request.method,
            "url": mask_url(flow.request.pretty_url),
            "status": flow.response.status_code,
            "ms": round((finished - started) * 1000),
            "request_type": flow.request.headers.get("content-type", ""),
            "request": mask_body(flow.request.get_text(strict=False) or "")[:4000],
            "response_type": flow.response.headers.get("content-type", ""),
            "response": mask_body(flow.response.get_text(strict=False) or "")[:4000],
        }
        with Path(target).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


addons = [Record()]

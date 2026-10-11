"""Дополнение mitmproxy: портит ответы по мутации из каталога.

    mitmdump --mode reverse:http://localhost:8090 -p 8091 -s src/pbqa/addons/mutate.py --set mutation=<имя>

Без опции mutation прокси ничего не меняет — так проверяется, что сам прокси прогону не мешает.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mitmproxy import ctx, http

from pbqa.mutations import BY_NAME, Response, apply


class Mutate:
    def __init__(self) -> None:
        self.touched = 0

    def load(self, loader: object) -> None:
        loader.add_option("mutation", str, "", "имя мутации из pbqa.mutations.CATALOG")  # type: ignore[attr-defined]
        loader.add_option("mutation_report", str, "", "куда записать, сколько ответов задела мутация")  # type: ignore[attr-defined]

    async def response(self, flow: http.HTTPFlow) -> None:
        name = ctx.options.mutation
        if not name or flow.response is None:
            return
        mutation = BY_NAME[name]
        headers = {k.lower(): v for k, v in flow.response.headers.items()}  # type: ignore[no-untyped-call]
        r = Response(
            url=flow.request.pretty_url,
            status=flow.response.status_code,
            headers=dict(headers),
            body=flow.response.get_text(strict=False) or "",
        )
        apply(mutation, r)
        if not r.touched:
            return
        self.touched += 1
        self._report()  # сразу: на Windows прокси останавливают без хука done
        if r.delay:
            await asyncio.sleep(r.delay)
        flow.response.status_code = r.status
        flow.response.set_text(r.body)
        for key, value in r.headers.items():
            if headers.get(key) != value:
                flow.response.headers[key] = value

    def _report(self) -> None:
        report = ctx.options.mutation_report
        if report:
            Path(report).write_text(json.dumps({"mutation": ctx.options.mutation, "touched": self.touched}))

    def done(self) -> None:
        self._report()


addons = [Mutate()]

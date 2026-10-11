"""Мутации ответов ParaBank: насколько сильны проверки коллекций.

Идея мутационного тестирования, перенесённая на API: прокси портит ответ сервера
одним известным способом, коллекция прогоняется заново. Если ни одна проверка не упала,
мутант «выжил» — значит, такую поломку в настоящем банке коллекция тоже пропустит.

Каждая мутация — чистая функция над ответом. Так её можно проверить без прокси и без сети,
а дополнение для mitmproxy остаётся тонкой обёрткой.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Response:
    url: str
    status: int
    headers: dict[str, str]
    body: str
    delay: float = 0.0
    touched: bool = field(default=False)


Mutator = Callable[[Response], None]


@dataclass(frozen=True, slots=True)
class Mutation:
    name: str
    description: str
    applies: Callable[[str], bool]
    mutate: Mutator


def _is_rest(url: str) -> bool:
    return "/services/bank/" in url


def _is_soap(url: str) -> bool:
    return "/services/ParaBank" in url


def _json_walk(value: Any, fn: Callable[[dict[str, Any]], None]) -> None:
    if isinstance(value, dict):
        fn(value)
        for item in value.values():
            _json_walk(item, fn)
    elif isinstance(value, list):
        for item in value:
            _json_walk(item, fn)


def _rewrite_json(fn: Callable[[dict[str, Any]], None]) -> Mutator:
    def mutate(r: Response) -> None:
        try:
            data = json.loads(r.body)
        except ValueError:
            return
        before = json.dumps(data, sort_keys=True)
        _json_walk(data, fn)
        after = json.dumps(data, sort_keys=True)
        if after != before:
            r.body, r.touched = json.dumps(data), True

    return mutate


def _plus_cent(obj: dict[str, Any]) -> None:
    if isinstance(obj.get("balance"), int | float):
        obj["balance"] = round(obj["balance"] + 0.01, 2)


def _drop_type(obj: dict[str, Any]) -> None:
    if {"id", "customerId", "type", "balance"} <= obj.keys():
        del obj["type"]


def _flip_sign(obj: dict[str, Any]) -> None:
    if obj.get("type") in ("Credit", "Debit") and "accountId" in obj:
        obj["type"] = "Debit" if obj["type"] == "Credit" else "Credit"


def _other_customer(obj: dict[str, Any]) -> None:
    if "firstName" in obj and "ssn" in obj:
        obj["firstName"] = "Ivan"


def _sub(pattern: str, repl: Callable[[re.Match[str]], str] | str, *, count: int = 0) -> Mutator:
    regex = re.compile(pattern)

    def mutate(r: Response) -> None:
        new = regex.sub(repl, r.body, count=count)
        if new != r.body:
            r.body, r.touched = new, True

    return mutate


def _soap_plus_cent(match: re.Match[str]) -> str:
    return f"<balance>{float(match.group(1)) + 0.01:.2f}</balance>"


def _status(code: int) -> Mutator:
    def mutate(r: Response) -> None:
        r.status, r.touched = code, True

    return mutate


def _slow(seconds: float) -> Mutator:
    def mutate(r: Response) -> None:
        r.delay, r.touched = seconds, True

    return mutate


def _json_as_text(r: Response) -> None:
    if r.body.lstrip()[:1] in ("{", "[") and "json" in r.headers.get("content-type", "").lower():
        r.headers["content-type"], r.touched = "text/plain", True


def _date_zero(obj: dict[str, Any]) -> None:
    if "date" in obj and "accountId" in obj:
        obj["date"] = 0


def _empty_list(r: Response) -> None:
    if r.body.lstrip().startswith("["):
        r.body, r.touched = "[]", True


CATALOG: tuple[Mutation, ...] = (
    Mutation("rest-balance-plus-cent", "REST: остаток счёта на цент больше", _is_rest, _rewrite_json(_plus_cent)),
    Mutation("rest-drop-account-type", "REST: у счёта пропал тип", _is_rest, _rewrite_json(_drop_type)),
    Mutation("rest-flip-transaction-sign", "REST: зачисление стало списанием", _is_rest, _rewrite_json(_flip_sign)),
    Mutation("rest-other-customer", "REST: в ответе чужое имя клиента", _is_rest, _rewrite_json(_other_customer)),
    Mutation(
        "rest-empty-accounts",
        "REST: список счетов пуст",
        lambda u: _is_rest(u) and u.rstrip("/").endswith("/accounts"),
        _empty_list,
    ),
    Mutation("rest-json-as-text", "REST: JSON объявлен как text/plain", _is_rest, _json_as_text),
    Mutation("rest-transaction-date-zero", "REST: дата операции — 1 января 1970", _is_rest, _rewrite_json(_date_zero)),
    Mutation("rest-transfer-500", "REST: перевод отвечает 500", lambda u: "/services/bank/transfer" in u, _status(500)),
    Mutation(
        "rest-transfer-wrong-amount",
        "REST: в подтверждении перевода другая сумма",
        lambda u: "/services/bank/transfer" in u,
        _sub(r"\$(\d+)\.50", r"$\g<1>.00"),
    ),
    Mutation(
        "soap-balance-plus-cent",
        "SOAP: остаток на цент больше",
        _is_soap,
        _sub(r"<balance>(-?\d+\.\d+)</balance>", _soap_plus_cent),
    ),
    Mutation("soap-drop-first-id", "SOAP: пропал первый элемент id", _is_soap, _sub(r"<id>\d+</id>", "", count=1)),
    Mutation(
        "soap-fault-server",
        "SOAP: ошибка клиента выдана за ошибку сервера",
        _is_soap,
        _sub(r"soap:Client", "soap:Server"),
    ),
    Mutation(
        "soap-approved-false",
        "SOAP: одобренный кредит стал отказом",
        _is_soap,
        _sub(r"<approved>true</approved>", "<approved>false</approved>"),
    ),
    Mutation("slow-accounts", "ответ по счёту на 3,5 с медленнее", lambda u: "/accounts/" in u, _slow(3.5)),
)
BY_NAME = {m.name: m for m in CATALOG}


def apply(mutation: Mutation, response: Response) -> Response:
    if mutation.applies(response.url):
        mutation.mutate(response)
    return response

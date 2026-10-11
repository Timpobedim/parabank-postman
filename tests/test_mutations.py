"""Каталог мутаций и маскирование — чистые функции, без прокси и сети."""

from __future__ import annotations

import json

import pytest

from pbqa import mutations
from pbqa.masking import mask_body, mask_url
from pbqa.mutations import BY_NAME, Response, apply

REST = "http://h/parabank/services/bank"
SOAP = "http://h/parabank/services/ParaBank"
ACCOUNT = {"id": 1, "customerId": 2, "type": "CHECKING", "balance": 415.5}


def _json(url: str, data: object) -> Response:
    return Response(url, 200, {"content-type": "application/json"}, json.dumps(data))


def test_balance_plus_cent() -> None:
    r = apply(BY_NAME["rest-balance-plus-cent"], _json(f"{REST}/accounts/1", ACCOUNT))
    assert r.touched
    assert json.loads(r.body)["balance"] == 415.51


def test_nested_accounts_list() -> None:
    r = apply(BY_NAME["rest-drop-account-type"], _json(f"{REST}/customers/2/accounts", [ACCOUNT, ACCOUNT]))
    assert all("type" not in a for a in json.loads(r.body))


def test_transaction_sign_flip_only_for_transactions() -> None:
    tx = {"id": 1, "accountId": 1, "type": "Credit", "date": 1, "amount": 1.0, "description": "x"}
    assert (
        json.loads(apply(BY_NAME["rest-flip-transaction-sign"], _json(f"{REST}/accounts/1/transactions", [tx])).body)[
            0
        ]["type"]
        == "Debit"
    )
    # тип счёта не трогается — у счёта нет accountId
    assert not apply(BY_NAME["rest-flip-transaction-sign"], _json(f"{REST}/accounts/1", ACCOUNT)).touched


def test_rest_mutation_ignores_soap() -> None:
    r = Response(SOAP, 200, {}, "<balance>1.00</balance>")
    assert not apply(BY_NAME["rest-balance-plus-cent"], r).touched


@pytest.mark.parametrize(
    ("name", "body", "expected"),
    [
        ("soap-balance-plus-cent", "<a><balance>-2300.00</balance></a>", "<a><balance>-2299.99</balance></a>"),
        ("soap-drop-first-id", "<id>1</id><id>2</id>", "<id>2</id>"),
        ("soap-fault-server", "<faultcode>soap:Client</faultcode>", "<faultcode>soap:Server</faultcode>"),
        ("soap-approved-false", "<approved>true</approved>", "<approved>false</approved>"),
    ],
)
def test_soap_mutations(name: str, body: str, expected: str) -> None:
    assert apply(BY_NAME[name], Response(SOAP, 200, {}, body)).body == expected


def test_status_and_delay() -> None:
    assert apply(BY_NAME["rest-transfer-500"], Response(f"{REST}/transfer?x=1", 200, {}, "ok")).status == 500
    assert apply(BY_NAME["slow-accounts"], _json(f"{REST}/accounts/1", ACCOUNT)).delay == 3.5


def test_wrong_amount_in_confirmation() -> None:
    r = apply(
        BY_NAME["rest-transfer-wrong-amount"],
        Response(f"{REST}/transfer", 200, {}, "Successfully transferred $25.50 from account #1 to account #2"),
    )
    assert "$25.00" in r.body


def test_json_as_text_only_for_json() -> None:
    r = apply(BY_NAME["rest-json-as-text"], _json(f"{REST}/accounts/1", ACCOUNT))
    assert r.headers["content-type"] == "text/plain"
    plain = Response(f"{REST}/transfer", 200, {"content-type": "application/json"}, "Successfully transferred")
    assert not apply(BY_NAME["rest-json-as-text"], plain).touched


def test_untouched_when_nothing_matches() -> None:
    r = apply(BY_NAME["rest-empty-accounts"], _json(f"{REST}/accounts/1", ACCOUNT))
    assert not r.touched


def test_catalog_names_unique_and_described() -> None:
    names = [m.name for m in mutations.CATALOG]
    assert len(names) == len(set(names))
    assert all(m.description for m in mutations.CATALOG)


@pytest.mark.parametrize(
    ("url", "masked"),
    [
        ("http://h/parabank/services/bank/login/qa1/Pw123", "http://h/parabank/services/bank/login/qa1/***"),
        ("http://h/parabank/services/bank/login/qa1/Pw123?x=1", "http://h/parabank/services/bank/login/qa1/***?x=1"),
        ("http://h/parabank/services/bank/accounts/1", "http://h/parabank/services/bank/accounts/1"),
    ],
)
def test_mask_url(url: str, masked: str) -> None:
    assert mask_url(url) == masked


def test_mask_body_soap_and_form() -> None:
    assert mask_body("<par:password>Pw1</par:password>") == "<par:password>***</par:password>"
    form = "customer.username=qa&customer.password=Pw1&repeatedPassword=Pw1"
    assert mask_body(form) == "customer.username=qa&customer.password=***&repeatedPassword=***"

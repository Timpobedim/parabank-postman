"""Маскирование паролей в журнале обмена."""

from __future__ import annotations

import re

MASK = "***"
_LOGIN_PATH = re.compile(r"(/login/[^/?#]+/)[^/?#]+")
_XML_PASSWORD = re.compile(r"(<(?:\w+:)?password>)[^<]*(</(?:\w+:)?password>)", re.IGNORECASE)
_FORM_PASSWORD = re.compile(r"((?:^|&)(?:customer\.password|repeatedPassword|password)=)[^&]*", re.IGNORECASE)


def mask_url(url: str) -> str:
    return _LOGIN_PATH.sub(rf"\g<1>{MASK}", url)


def mask_body(body: str) -> str:
    body = _XML_PASSWORD.sub(rf"\g<1>{MASK}\g<2>", body)
    return _FORM_PASSWORD.sub(rf"\g<1>{MASK}", body)

import pytest
from controlplane.guardrails.pii import _EMAIL_RE, _PHONE_RE, _SSN_RE, _CREDIT_CARD_RE, _OPENAI_KEY_RE, _AWS_KEY_RE

def test_email_re():
    assert _EMAIL_RE.search("test@example.com") is not None
    assert _EMAIL_RE.search("my.name+tag@domain.co.uk") is not None
    assert _EMAIL_RE.search("not_an_email") is None

def test_phone_re():
    assert _PHONE_RE.search("555-123-4567") is not None
    assert _PHONE_RE.search("(555) 123-4567") is not None
    assert _PHONE_RE.search("1234") is None

def test_ssn_re():
    assert _SSN_RE.search("123-45-6789") is not None
    assert _SSN_RE.search("123456789") is None

def test_credit_card_re():
    assert _CREDIT_CARD_RE.search("1234-5678-9012-3456") is not None
    assert _CREDIT_CARD_RE.search("1234567890123456") is not None
    assert _CREDIT_CARD_RE.search("123") is None

def test_openai_key_re():
    assert _OPENAI_KEY_RE.search("sk-12345678901234567890") is not None
    assert _OPENAI_KEY_RE.search("sk-short") is None

def test_aws_key_re():
    assert _AWS_KEY_RE.search("AKIA1234567890ABCDEF") is not None
    assert _AWS_KEY_RE.search("AKIA123") is None

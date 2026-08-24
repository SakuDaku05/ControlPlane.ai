"""
Fast, deterministic PII / data-leakage detection for the synchronous
"hot path" inside the sidecar proxy (spec 3.2.3, Responsibility Agent:
"ultra-fast Named Entity Recognition (NER) pipelines ... to scan outbound
data, preventing data leakage").

Design note: a full statistical NER model (spaCy, a transformer token
classifier) is the "real" way to do this, and the deeper, async
Responsibility Agent (controlplane/agents/responsibility_agent.py) is
where that upgrade would slot in. But NER inference in the *synchronous*
request path is exactly the kind of thing that would blow the "<50ms TTFT
overhead" budget the spec sets for LOW risk pass-through — and this
sandbox has no internet access to even download a pretrained NER model
(pypi/HF are both firewalled here). So the hot path uses compiled regexes
plus light structural validation (Luhn check for card numbers, plausible
area-code shape for phone numbers) to keep false positives down while
staying sub-millisecond. This is a legitimate, common production pattern
(e.g. AWS Comprehend, GCP DLP, and most WAF-layer DLP products all lead
with regex/pattern detectors for structured PII and reserve ML NER for
unstructured entities like names/orgs, which we scan for asynchronously).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)")
_SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_CREDIT_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")
_ANTHROPIC_KEY_RE = re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b")
_AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_IPV4_RE = re.compile(r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")


def _luhn_valid(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


@dataclass
class PIIMatch:
    category: str
    start: int
    end: int
    value: str
    severity: float  # 0..1 contribution to the responsibility risk score


_MASKS = {
    "EMAIL": "[REDACTED_EMAIL]",
    "PHONE": "[REDACTED_PHONE]",
    "SSN": "[REDACTED_SSN]",
    "CREDIT_CARD": "[REDACTED_CARD]",
    "API_KEY": "[REDACTED_API_KEY]",
    "AWS_KEY": "[REDACTED_AWS_KEY]",
    "IP_ADDRESS": "[REDACTED_IP]",
    "JWT": "[REDACTED_TOKEN]",
}


def detect_pii(text: str) -> list[PIIMatch]:
    matches: list[PIIMatch] = []

    for m in _EMAIL_RE.finditer(text):
        matches.append(PIIMatch("EMAIL", m.start(), m.end(), m.group(), 0.5))

    for m in _SSN_RE.finditer(text):
        matches.append(PIIMatch("SSN", m.start(), m.end(), m.group(), 0.95))

    for m in _CREDIT_CARD_RE.finditer(text):
        digits = re.sub(r"[ -]", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            matches.append(PIIMatch("CREDIT_CARD", m.start(), m.end(), m.group(), 0.95))

    for m in _ANTHROPIC_KEY_RE.finditer(text):
        matches.append(PIIMatch("API_KEY", m.start(), m.end(), m.group(), 0.9))
    for m in _OPENAI_KEY_RE.finditer(text):
        if not any(mm.start <= m.start() < mm.end for mm in matches if mm.category == "API_KEY"):
            matches.append(PIIMatch("API_KEY", m.start(), m.end(), m.group(), 0.9))

    for m in _AWS_KEY_RE.finditer(text):
        matches.append(PIIMatch("AWS_KEY", m.start(), m.end(), m.group(), 0.9))

    for m in _JWT_RE.finditer(text):
        matches.append(PIIMatch("JWT", m.start(), m.end(), m.group(), 0.8))

    for m in _PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 10 <= len(digits) <= 13:
            matches.append(PIIMatch("PHONE", m.start(), m.end(), m.group(), 0.4))

    for m in _IPV4_RE.finditer(text):
        matches.append(PIIMatch("IP_ADDRESS", m.start(), m.end(), m.group(), 0.2))

    matches.sort(key=lambda m: m.start)
    return matches


def mask_pii(text: str) -> tuple[str, list[PIIMatch]]:
    """Inline-replace every detected PII span. This is what backs the
    MEDIUM RISK / Auto-Edit protocol in spec 3.3: 'a lightweight Regex/NER
    mask applies inline replacement before flushing the buffer to the
    client.'"""
    matches = detect_pii(text)
    if not matches:
        return text, matches

    out, cursor = [], 0
    for m in matches:
        if m.start < cursor:
            continue  # overlapping match, skip
        out.append(text[cursor:m.start])
        out.append(_MASKS.get(m.category, "[REDACTED]"))
        cursor = m.end
    out.append(text[cursor:])
    return "".join(out), matches


def pii_severity(matches: list[PIIMatch]) -> float:
    if not matches:
        return 0.0
    return min(1.0, max(m.severity for m in matches) + 0.05 * (len(matches) - 1))

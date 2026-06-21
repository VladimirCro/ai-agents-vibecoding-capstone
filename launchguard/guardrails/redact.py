"""
launchguard.guardrails.redact — Secret redaction pass (AI Operating Principles §3).

Every payload that goes to Gemini AND every trace/log line MUST pass through redact()
before emission.  The redaction function:

  - Masks values that look like secrets/tokens/keys/connection-strings/PII
  - Preserves secret NAMES and existence information (the Reconciler needs names)
  - Returns a copy — never mutates the input

Heuristic approach:
  1. Pattern matching on well-known secret value shapes (base64-like long strings,
     connection strings, JWT tokens, GCP service-account JSON, etc.)
  2. Key-name heuristics: if a dict key contains "key", "secret", "token", "password",
     "credential", "private", "dsn", "connection_string" → mask the value
  3. Known safe fields: "name", "accessor_members", "locator", "source", "rule_id",
     "delta_class", "summary", "code", "message" etc. → always pass through

This is a best-effort redactor.  LLM-bound payloads should be structured so that
secret VALUES never appear in them at all (architecture §6 principle).  This function
is the defensive layer.

Usage:
    from launchguard.guardrails.redact import redact, redact_snapshot

    safe_payload = redact(model_input_dict)
    safe_fixture  = redact_snapshot(raw_gcp_dict)
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REDACTED_MARKER: str = "[REDACTED]"

# Key name substrings that signal a secret VALUE (case-insensitive).
# If a dict key contains any of these, the value is masked.
_SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "privatekey",
    "credential",
    "credentials",
    "connection_string",
    "conn_string",
    "dsn",
    "auth_key",
    "auth_token",
    "access_token",
    "refresh_token",
    "client_secret",
    "webhook_secret",
    "signing_key",
    "encryption_key",
)

# Fields that are safe to pass through even if key name looks sensitive.
# These carry metadata (names, existence), never values.
_SAFE_PASSTHROUGH_KEYS: frozenset[str] = frozenset({
    "name",
    "secret_refs",
    "accessor_members",
    "source",
    "locator",
    "rule_id",
    "delta_class",
    "code",
    "message",
    "summary",
    "recommendation",
    "confidence",
    "verdict",
    "mode",
    "project_id",
    "runtime_sa",
    "sa_iam_roles",
    "enabled_apis",
    "secrets",                # list of {name, accessor_members} — values never present
    "secret_refs",            # list of secret NAMES from IntendedContract/DeclaredState
    "secret_env_ref_names",   # list of secret NAMES from run_config — names, not values
    "evidence",
    "matches",
    "file",
    "line",
    "text",         # grep_code output — already redacted at source
})

# Regex patterns that match secret-looking VALUES in plain text.
# Order matters: longest/most-specific first.
_VALUE_PATTERNS: list[re.Pattern[str]] = [
    # GCP Service Account JSON key patterns
    re.compile(r'"private_key"\s*:\s*"-----BEGIN[^"]*"', re.DOTALL),
    re.compile(r'"private_key_id"\s*:\s*"[A-Fa-f0-9]{40}"'),
    # JWT (three base64url segments separated by dots)
    re.compile(r'eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'),
    # Long base64-like strings (> 40 chars with no spaces — likely a key/token)
    re.compile(r'[A-Za-z0-9+/]{40,}={0,2}'),
    # Hex strings 64+ chars (e.g. SHA-256 keys)
    re.compile(r'\b[A-Fa-f0-9]{64,}\b'),
    # GCP API keys (AIza prefix)
    re.compile(r'AIza[A-Za-z0-9_-]{35}'),
    # Generic "key=VALUE" patterns with long values
    re.compile(r'(?i)(password|passwd|secret|token|key|credential)[\s=:]+[^\s,;"]{8,}'),
    # Connection strings (postgres://, mysql://, redis://)
    re.compile(r'(?i)(postgres|postgresql|mysql|redis|mongodb|amqp)://[^\s"\']+'),
    # SMTP/email credentials in URLs
    re.compile(r'(?i)smtp[s]?://[^\s"\']+'),
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def redact(payload: Any) -> Any:
    """
    Recursively redact a payload (dict, list, str, or scalar).

    - dict keys in _SAFE_PASSTHROUGH_KEYS → value passed through unchanged
    - dict keys containing _SECRET_KEY_PATTERNS → value replaced with REDACTED_MARKER
    - str values → _redact_string() applied
    - list / tuple → each element recursively redacted
    - Other scalars (int, bool, None, float) → passed through unchanged

    Returns a deep copy with secrets masked.  Input is never mutated.

    AI Operating Principles §3: call this before every model payload AND trace output.
    """
    return _redact_value(payload, parent_key=None)


def redact_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Redact a raw GCP LiveState snapshot before persisting to fixtures/gcp/.

    Equivalent to redact() but typed to dict → dict for the fixture persistence path.
    Ensures:
      - No secret values in the snapshot
      - accessor_members preserved (membership, not values)
      - Secret names preserved (needed for reconciliation)

    AI Operating Principles §6: fixtures are redacted at capture time.
    """
    result = redact(raw)
    assert isinstance(result, dict), "redact_snapshot input must be a dict"
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _redact_value(value: Any, parent_key: str | None) -> Any:
    """Recursively redact a value in context of its parent dict key."""
    if isinstance(value, dict):
        return _redact_dict(value)
    elif isinstance(value, (list, tuple)):
        redacted_list = [_redact_value(item, parent_key) for item in value]
        return type(value)(redacted_list)
    elif isinstance(value, str):
        # If parent key is a known-safe field, don't redact the string
        if parent_key is not None and parent_key in _SAFE_PASSTHROUGH_KEYS:
            return value
        return _redact_string(value)
    else:
        # int, float, bool, None — safe
        return value


def _redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Redact a dict: key-sensitive masking + recursive value redaction."""
    result: dict[str, Any] = {}
    for key, value in d.items():
        if not isinstance(key, str):
            # Non-string keys — redact value defensively
            result[key] = _redact_value(value, parent_key=None)
            continue

        key_lower = key.lower()

        # Safe passthrough keys — never mask the value
        if key_lower in _SAFE_PASSTHROUGH_KEYS:
            result[key] = value
            continue

        # Key name matches a secret pattern → mask the value entirely
        if _key_looks_like_secret(key_lower):
            result[key] = REDACTED_MARKER
            continue

        # Otherwise recurse into the value
        result[key] = _redact_value(value, parent_key=key_lower)

    return result


def _key_looks_like_secret(key_lower: str) -> bool:
    """Return True if the key name suggests a secret value (not a secret name)."""
    return any(pattern in key_lower for pattern in _SECRET_KEY_PATTERNS)


def _redact_string(text: str) -> str:
    """Apply VALUE pattern matching to a plain text string."""
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(REDACTED_MARKER, text)
    return text

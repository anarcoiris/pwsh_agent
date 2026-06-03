"""Extract inline user/password values from login prompts (quote-aware)."""
from __future__ import annotations

import re


def _read_quoted_value(text: str) -> str | None:
    """Parse a leading single- or double-quoted string (handles escapes)."""
    if not text or text[0] not in "\"'":
        return None
    quote = text[0]
    i = 1
    out: list[str] = []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        if ch == quote:
            return "".join(out)
        out.append(ch)
        i += 1
    return None


def _extract_labeled_value(message: str, label: str) -> str | None:
    """Return value after ``user:`` / ``password:`` (quoted or unquoted)."""
    pat = rf"\b{label}\s*[:=]\s*"
    m = re.search(pat, message, re.I)
    if not m:
        return None
    rest = message[m.end() :].lstrip()
    if rest.startswith(('"', "'")):
        quoted = _read_quoted_value(rest)
        if quoted is not None:
            # Handle ``password: "abc"/"`` where ``/"`` sits outside the closing quote.
            if label.lower().startswith("pass") and rest[0] == '"':
                tail = rest[1 + len(quoted) + 1 :]
                if tail.startswith('/"'):
                    return quoted + '"/'
            return quoted
    # Unquoted token — stop at whitespace or common clause boundaries.
    um = re.match(r"(\S+)", rest)
    if um:
        return um.group(1).strip("\"'")
    return None


def extract_web_auth_credentials(message: str) -> dict[str, str]:
    """
    Pull username and password from natural-language login prompts.

    Handles passwords with embedded quotes, slashes, and newlines inside quotes,
    e.g. ``password: "Qtpowppu27"/"`` or a multiline quoted password.
    """
    if not message:
        return {}
    creds: dict[str, str] = {}
    user = _extract_labeled_value(message, r"user(?:name)?")
    if user and user.lower() not in ("name", "and", "with"):
        creds["user"] = user
    pwd = _extract_labeled_value(message, r"pass(?:word|wd)?")
    if pwd:
        creds["password"] = pwd
    return creds


def reconcile_login_args(
    tool_args: dict,
    anchor_credentials: dict[str, str],
) -> tuple[dict, bool]:
    """
    Prefer anchor-query credentials when the LLM mangled special characters.

    Returns (updated_args, was_corrected).
    """
    if not anchor_credentials:
        return tool_args, False
    updated = dict(tool_args)
    corrected = False
    for key in ("user", "password"):
        anchor_val = anchor_credentials.get(key)
        if not anchor_val:
            continue
        llm_val = str(updated.get(key, "") or "")
        if llm_val != anchor_val:
            updated[key] = anchor_val
            corrected = True
    return updated, corrected

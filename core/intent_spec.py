"""
core/intent_spec.py — Intent Formalization layer (Phase 1, shadow mode).

Translates a raw user message into a structured "declaration of intent"
(`IntentSpec`): domain, objectives, targets, capabilities, success criteria,
and a safety assessment.

Design notes (see docs/plans/Generalization/multi_purpose_agent_design.md):
- A deterministic regex *fallback* builder always produces a usable spec with
  no LLM call. This keeps the layer testable and crash-proof.
- An optional `IntentFormalizer` uses a small LLM call to produce a richer spec;
  its output is merged over the fallback (LLM wins; fallback fills gaps).
- Phase 1 runs in SHADOW MODE: the spec is computed, persisted, and logged, but
  does NOT yet gate routing/planning/completion. That wiring is later phases.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from core.task_intent import TaskIntentExtractor, detect_mission_kind

# ──────────────────────────────────────────────────────────────────────────────
# Vocabulary
# ──────────────────────────────────────────────────────────────────────────────

# Known domains. "mixed"/"general" are catch-alls; unknown LLM values fall back
# to these rather than being rejected.
DOMAINS = frozenset({
    "web_auth", "recon", "pcap", "hash", "code_review", "code_build",
    "scripting", "sysadmin", "file_ops", "reporting", "conversation",
    "mixed", "general",
})

# Capability tags the planner/registry will resolve to concrete tools (later
# phases). Free-form is allowed, but these are the canonical ones.
CAPABILITIES = frozenset({
    "file_read", "file_write", "file_edit",
    "http_auth_attempt", "http_inspect", "http_fetch",
    "port_scan", "dns_lookup", "ping_sweep", "ssl_inspect",
    "pcap_analyze", "hash_crack", "hash_identify", "encode_decode",
    "code_review", "static_scan", "code_build", "scaffold",
    "scripting", "task_schedule", "system_info",
    "cve_lookup", "reporting", "conversation",
})

# Mission kinds from the legacy classifier → coarse domain seed.
_MISSION_TO_DOMAIN = {
    "hash": "hash",
    "pcap": "pcap",
    "dev": "code_build",
    "file_find": "file_ops",
    "recon": "recon",
    "general": "general",
}

_DOMAIN_CAPABILITIES = {
    "web_auth": ["http_auth_attempt", "http_inspect"],
    "recon": ["port_scan", "dns_lookup", "ping_sweep", "http_inspect"],
    "pcap": ["pcap_analyze", "file_read"],
    "hash": ["hash_crack", "hash_identify"],
    "code_review": ["code_review", "file_read", "static_scan"],
    "code_build": ["code_build", "file_write"],
    "scripting": ["scripting", "file_write"],
    "sysadmin": ["system_info", "task_schedule"],
    "file_ops": ["file_read", "file_edit", "file_write"],
    "reporting": ["reporting"],
    "conversation": ["conversation"],
    "general": [],
    "mixed": [],
}

# ──────────────────────────────────────────────────────────────────────────────
# Extraction regexes (fallback seeding)
# ──────────────────────────────────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://[^\s'\"<>)\]]+", re.I)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_AUTH_RE = re.compile(
    r"\b(log\s?in|login|sign\s?in|authenticate|auth|credential|brute[- ]?force\s+login)\b",
    re.I,
)
_USERPASS_RE = re.compile(r"\b(user(name)?|password|passwd|pwd)\b", re.I)
_REVIEW_RE = re.compile(r"\b(review|audit|critique|inspect|analy[sz]e)\b.*\b(code|script|function|file|\.py|\.ps1|\.js|\.ts)\b", re.I | re.S)
_SCRIPT_BUILD_RE = re.compile(r"\b(write|create|build|generate|make)\b.*\b(powershell|\.ps1|script|cmdlet)\b", re.I | re.S)
_SCHEDULE_RE = re.compile(r"\b(scheduled?\s+task|schtasks|task\s+scheduler|register-scheduledtask|cron job)\b", re.I)
_SERVICE_RE = re.compile(r"\b(start|stop|restart|disable|enable)\b.*\b(service|process|daemon)\b", re.I)
_FILE_EDIT_RE = re.compile(r"\b(rename|move|edit|revise|refactor|modify|update|patch|fix)\b.*\b(file|\.py|\.ps1|\.md|\.txt|\.json|directory|folder)\b", re.I | re.S)
_DESTRUCTIVE_RE = re.compile(r"\b(delete|remove|rm\b|format|wipe|erase|drop\s+table|kill)\b", re.I)
_QUESTION_RE = re.compile(r"\b(what|why|how|when|where|which|who|explain|describe|tell me|can you|should i)\b", re.I)


# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SafetyAssessment:
    network_egress: bool = False
    destructive: bool = False
    system_modification: bool = False
    needs_confirmation: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SafetyAssessment":
        data = data or {}
        return cls(
            network_egress=bool(data.get("network_egress", False)),
            destructive=bool(data.get("destructive", False)),
            system_modification=bool(data.get("system_modification", False)),
            needs_confirmation=bool(data.get("needs_confirmation", False)),
            notes=str(data.get("notes", "")),
        )


@dataclass
class IntentSpec:
    """Structured declaration of what the user wants, driving later layers."""

    raw: str = ""
    summary: str = ""
    domain: str = "general"
    objectives: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    inputs: dict[str, str] = field(default_factory=dict)
    deliverables: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    safety: SafetyAssessment = field(default_factory=SafetyAssessment)
    confidence: float = 0.0
    needs_clarification: list[str] = field(default_factory=list)
    source: str = "fallback"  # "fallback" | "llm" | "llm+fallback"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["safety"] = self.safety.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IntentSpec":
        data = data or {}
        return cls(
            raw=str(data.get("raw", "")),
            summary=str(data.get("summary", "")),
            domain=_coerce_domain(data.get("domain")),
            objectives=_str_list(data.get("objectives")),
            targets=_str_list(data.get("targets")),
            inputs=_str_dict(data.get("inputs")),
            deliverables=_str_list(data.get("deliverables")),
            constraints=_str_list(data.get("constraints")),
            success_criteria=_str_list(data.get("success_criteria")),
            capabilities=_str_list(data.get("capabilities")),
            safety=SafetyAssessment.from_dict(data.get("safety")),
            confidence=_coerce_float(data.get("confidence")),
            needs_clarification=_str_list(data.get("needs_clarification")),
            source=str(data.get("source", "fallback")),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Coercion helpers
# ──────────────────────────────────────────────────────────────────────────────

def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    if isinstance(value, (list, tuple, set)):
        for v in value:
            s = str(v).strip()
            if s and s not in out:
                out.append(s)
    return out


def _str_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if str(k).strip()}


def _coerce_float(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


def _coerce_domain(value: Any) -> str:
    s = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if s in DOMAINS:
        return s
    return "mixed" if s else "general"


# ──────────────────────────────────────────────────────────────────────────────
# Fallback builder (deterministic, no LLM)
# ──────────────────────────────────────────────────────────────────────────────

def _detect_domain(message: str) -> str:
    """Heuristic domain detection that is broader than detect_mission_kind."""
    text = message or ""
    lower = text.lower()
    has_target = bool(_URL_RE.search(text) or _IP_RE.search(text))

    # web_auth: an auth/login intent against a network target, or user+password
    # mentioned alongside a URL/IP (the incident pattern).
    if (_AUTH_RE.search(lower) and has_target) or (
        _USERPASS_RE.search(lower) and has_target and "hash" not in lower
    ):
        return "web_auth"

    # Hash only when explicitly about hashing/cracking (mirrors legacy classifier).
    mission = detect_mission_kind(text)
    if mission == "hash":
        return "hash"
    if mission == "pcap":
        return "pcap"

    if _SCHEDULE_RE.search(lower) or _SERVICE_RE.search(lower):
        return "sysadmin"
    if _REVIEW_RE.search(text):
        return "code_review"
    if _SCRIPT_BUILD_RE.search(text):
        return "scripting"
    if _FILE_EDIT_RE.search(text):
        return "file_ops"

    if mission == "file_find":
        return "file_ops"
    if mission == "dev":
        return "code_build"
    if mission == "recon":
        return "recon"

    # Pure question with no actionable verb → conversation.
    if _QUESTION_RE.search(lower) and not re.search(r"\b(run|scan|crack|write|create|build|extract|attempt|try)\b", lower):
        return "conversation"

    return "general"


_FETCH_INTENT_RE = re.compile(
    r"\b(get|fetch|download|retrieve|curl|wget|scrape|grab|obtener|descargar?|baja[r]?)\b"
    r"|\bhtml\b|\bweb\s?page\b|\bwebpage\b|\bindex\.html?\b",
    re.I,
)


def _seed_capabilities(domain: str, message: str) -> list[str]:
    caps = list(_DOMAIN_CAPABILITIES.get(domain, []))
    lower = (message or "").lower()
    # Fetching the body of a remote URL/IP is an HTTP GET (http_fetch/http_inspect),
    # NOT packet capture. Surface the fetch tool whenever a web target is present
    # alongside a get/fetch/download intent, regardless of the coarse domain.
    if (_URL_RE.search(message or "") or _IP_RE.search(message or "")) and _FETCH_INTENT_RE.search(lower):
        for c in ("http_fetch", "http_inspect"):
            if c not in caps:
                caps.append(c)
    if re.search(r"\bcve\b|vulnerabilit", lower):
        caps.append("cve_lookup")
    if re.search(r"\bport\b|\bnmap\b", lower):
        caps.append("port_scan")
    if re.search(r"\bdns\b|resolve", lower):
        caps.append("dns_lookup")
    if re.search(r"\bssl\b|\btls\b|certificate|\bcert\b", lower):
        caps.append("ssl_inspect")
    if re.search(r"\bbase64\b|encode|decode|\bhex\b|rot13", lower):
        caps.append("encode_decode")
    if re.search(r"\breport\b|findings?\b", lower):
        caps.append("reporting")
    # Reading a referenced file is implied when a path/source is mentioned.
    if re.search(r"\b[\w./\\-]+\.(txt|md|json|ya?ml|py|ps1|log|pcapng?)\b", lower):
        caps.append("file_read")
    # De-dup, preserve order.
    seen: list[str] = []
    for c in caps:
        if c not in seen:
            seen.append(c)
    return seen


def _assess_safety(domain: str, message: str, targets: list[str]) -> SafetyAssessment:
    lower = (message or "").lower()
    egress = domain in ("web_auth", "recon", "pcap") and bool(targets)
    egress = egress or bool(_URL_RE.search(message or "") or _IP_RE.search(message or ""))
    destructive = bool(_DESTRUCTIVE_RE.search(lower))
    sysmod = domain == "sysadmin" or bool(_SCHEDULE_RE.search(lower) or _SERVICE_RE.search(lower))
    needs_confirm = egress or destructive or sysmod
    notes = []
    if egress:
        notes.append("sends traffic off-host")
    if destructive:
        notes.append("potentially destructive operation")
    if sysmod:
        notes.append("modifies system configuration")
    return SafetyAssessment(
        network_egress=egress,
        destructive=destructive,
        system_modification=sysmod,
        needs_confirmation=needs_confirm,
        notes="; ".join(notes),
    )


def _extract_inputs(message: str) -> dict[str, str]:
    inputs: dict[str, str] = {}
    # user: X  /  user is X  /  username X
    m = re.search(r"\buser(?:name)?\s*[:=]?\s*[\"']?([\w.@\\-]+)[\"']?", message, re.I)
    if m and m.group(1).lower() not in ("name", "and"):
        inputs["user"] = m.group(1)
    # password source: a quoted file path or the word password followed by a path
    pm = re.search(r"password\s*[:=]?\s*[\"']([^\"']+\.(?:txt|md|json|cfg|conf))[\"']", message, re.I)
    if pm:
        inputs["password_source"] = pm.group(1).replace("\\", "/")
    return inputs


def build_fallback_spec(message: str) -> IntentSpec:
    """Deterministic IntentSpec with no LLM call. Always safe to call."""
    text = message or ""
    domain = _detect_domain(text)

    targets: list[str] = []
    for m in _URL_RE.finditer(text):
        t = m.group(0).rstrip(".,);")
        if t not in targets:
            targets.append(t)
    for m in _IP_RE.finditer(text):
        ip = m.group(0)
        # Skip bare IPs already represented inside a URL target.
        if ip in targets or any(ip in t for t in targets):
            continue
        targets.append(ip)

    intent = TaskIntentExtractor.parse(text)
    deliverables = list(intent.deliverables)

    constraints: list[str] = []
    if intent.forbid_network:
        constraints.append("do not use network/recon tools")

    inputs = _extract_inputs(text)
    # A referenced password file is both an input and a file to read.
    for v in inputs.values():
        if v.endswith((".txt", ".md", ".json", ".cfg", ".conf")) and v not in targets:
            targets.append(v)

    capabilities = _seed_capabilities(domain, text)
    safety = _assess_safety(domain, text, targets)

    summary = re.sub(r"\s+", " ", text.strip())
    if len(summary) > 160:
        summary = summary[:157] + "..."

    return IntentSpec(
        raw=text,
        summary=summary,
        domain=domain,
        objectives=[],
        targets=targets,
        inputs=inputs,
        deliverables=deliverables,
        constraints=constraints,
        success_criteria=[],
        capabilities=capabilities,
        safety=safety,
        confidence=0.4,
        needs_clarification=[],
        source="fallback",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Merge (LLM over fallback)
# ──────────────────────────────────────────────────────────────────────────────

def merge_specs(fallback: IntentSpec, llm: IntentSpec) -> IntentSpec:
    """LLM-derived fields win; fallback fills any empty field."""
    def pick_list(a: list, b: list) -> list:
        return a if a else b

    def pick_str(a: str, b: str) -> str:
        return a if a.strip() else b

    safety = llm.safety
    # Safety is the union of concerns — never downgrade the fallback's flags.
    merged_safety = SafetyAssessment(
        network_egress=safety.network_egress or fallback.safety.network_egress,
        destructive=safety.destructive or fallback.safety.destructive,
        system_modification=safety.system_modification or fallback.safety.system_modification,
        needs_confirmation=safety.needs_confirmation or fallback.safety.needs_confirmation,
        notes=pick_str(safety.notes, fallback.safety.notes),
    )

    return IntentSpec(
        raw=fallback.raw,
        summary=pick_str(llm.summary, fallback.summary),
        domain=llm.domain if llm.domain not in ("general", "mixed") else fallback.domain,
        objectives=pick_list(llm.objectives, fallback.objectives),
        targets=pick_list(llm.targets, fallback.targets) or fallback.targets,
        inputs=llm.inputs or fallback.inputs,
        deliverables=pick_list(llm.deliverables, fallback.deliverables),
        constraints=pick_list(llm.constraints, fallback.constraints),
        success_criteria=pick_list(llm.success_criteria, fallback.success_criteria),
        capabilities=pick_list(llm.capabilities, fallback.capabilities),
        safety=merged_safety,
        confidence=max(llm.confidence, fallback.confidence),
        needs_clarification=pick_list(llm.needs_clarification, fallback.needs_clarification),
        source="llm+fallback",
    )


# ──────────────────────────────────────────────────────────────────────────────
# LLM formalizer
# ──────────────────────────────────────────────────────────────────────────────

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_SCHEMA_PROMPT = (
    "You translate a user's request to a security/sysadmin/coding agent into a "
    "STRICT JSON declaration of intent. Output JSON ONLY — no prose, no markdown.\n\n"
    "Keys:\n"
    "  summary: one sentence restating the goal.\n"
    f"  domain: one of {sorted(DOMAINS)}.\n"
    "  objectives: ordered array of concrete sub-goals.\n"
    "  targets: array of hosts/URLs/files/dirs the task acts on.\n"
    "  inputs: object of resolved parameters (e.g. user, password_source).\n"
    "  deliverables: array of files/artifacts the user expects.\n"
    "  constraints: array of limits (e.g. 'only edit dir X', 'no network').\n"
    "  success_criteria: array describing how we know the task is done.\n"
    f"  capabilities: array from {sorted(CAPABILITIES)} (extend only if needed).\n"
    "  safety: object {network_egress, destructive, system_modification, needs_confirmation, notes}.\n"
    "  confidence: 0..1.\n"
    "  needs_clarification: array of questions if the request is ambiguous.\n\n"
    "RULES:\n"
    "- A plaintext password to TEST against a login is NOT a hash. Use domain "
    "'web_auth' and capability 'http_auth_attempt' — never 'hash'/'hash_crack' "
    "unless the user explicitly asks to crack/identify a hash digest.\n"
    "- Set safety.network_egress=true whenever the task contacts a remote host/URL.\n\n"
    "EXAMPLE\n"
    "User: try user 'admin' with password from creds.txt against http://10.0.0.1\n"
    "JSON: {\"summary\":\"Attempt an HTTP login to 10.0.0.1 using admin and a "
    "password read from creds.txt\",\"domain\":\"web_auth\",\"objectives\":[\"read "
    "creds.txt\",\"attempt login as admin\",\"report auth result\"],\"targets\":"
    "[\"http://10.0.0.1\",\"creds.txt\"],\"inputs\":{\"user\":\"admin\","
    "\"password_source\":\"creds.txt\"},\"deliverables\":[],\"constraints\":[],"
    "\"success_criteria\":[\"a definitive auth success/fail with HTTP evidence\"],"
    "\"capabilities\":[\"file_read\",\"http_auth_attempt\"],\"safety\":"
    "{\"network_egress\":true,\"destructive\":false,\"system_modification\":false,"
    "\"needs_confirmation\":true,\"notes\":\"sends login traffic to a remote host\"},"
    "\"confidence\":0.9,\"needs_clarification\":[]}\n"
)


class IntentFormalizer:
    """Optional LLM-backed formalizer. Mirrors MissionEvaluator's lightweight call."""

    def __init__(self, host: str, model: str, temperature: float = 0.1, timeout: float = 60.0):
        # Imported lazily so the module is importable without ollama installed
        # (the fallback path needs no LLM and is used by tests).
        import httpx
        from ollama import AsyncClient

        self.host = host
        self.model = model
        self.temperature = temperature
        self.client = AsyncClient(host=host, timeout=httpx.Timeout(timeout))

    async def formalize(self, message: str) -> IntentSpec:
        """Return a merged IntentSpec; never raises (falls back on any error)."""
        fallback = build_fallback_spec(message)
        try:
            resp = await self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SCHEMA_PROMPT},
                    {"role": "user", "content": (message or "").strip()[:2000]},
                ],
                options={"temperature": self.temperature, "num_predict": 768},
                format="json",
                stream=False,
            )
            content = (resp.message.content or "").strip()
        except Exception:
            return fallback

        m = _JSON_RE.search(content)
        if not m:
            return fallback
        try:
            data = json.loads(m.group(0))
        except (json.JSONDecodeError, TypeError):
            return fallback
        if not isinstance(data, dict):
            return fallback

        data["raw"] = message or ""
        data["source"] = "llm"
        llm_spec = IntentSpec.from_dict(data)
        return merge_specs(fallback, llm_spec)


# ──────────────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────────────

def save_intent_spec(session_id: str, spec: IntentSpec) -> Any:
    """Append the spec to the session's intent_spec.json history (newest last)."""
    from core.session_paths import intent_spec_file

    path = intent_spec_file(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = loaded
        except (OSError, json.JSONDecodeError):
            history = []
    history.append(spec.to_dict())
    # Keep the file bounded.
    history = history[-50:]
    try:
        path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except OSError:
        return None
    return path


def load_latest_intent_spec(session_id: str) -> IntentSpec | None:
    from core.session_paths import intent_spec_file

    path = intent_spec_file(session_id)
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(loaded, list) and loaded:
        return IntentSpec.from_dict(loaded[-1])
    if isinstance(loaded, dict):
        return IntentSpec.from_dict(loaded)
    return None

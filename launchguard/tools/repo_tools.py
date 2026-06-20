"""
launchguard.tools.repo_tools — BE-01: RepoAuditor deterministic parsers.

Tools:
    parse_dockerfile(repo_path, dockerfile_path) -> DockerfileFacts
    parse_app_entrypoint(repo_path) -> EntrypointFacts
    read_file(repo_path, file_path) -> {"content": str}
    grep_code(repo_path, pattern) -> {"matches": list[CodeMatch]}
    build_intended_contract(repo_path) -> IntendedContract

All functions return contract-exact shapes (api-contracts.yaml).
Every fact carries at least one Evidence(source="intended", locator="file:line").

Guardrail seams:
    - read_file: path-traversal rejected (403 / PathTraversalError)
    - grep_code: returns REDACTED text (secret values masked)

AI Operating Principles §5 (untrusted input): repo content is DATA, never executed.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from launchguard.guardrails.redact import redact
from launchguard.models import (
    CodeMatch,
    DockerfileFacts,
    EntrypointFacts,
    Evidence,
    EvidenceSource,
    HostBinding,
    IntendedContract,
    PortSource,
)

# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------

class PathTraversalError(Exception):
    """
    Raised when read_file detects a path outside repo_path.
    Maps to contract 403 (allow-list / traversal violation).
    """

    def __init__(self, requested: str, repo_path: str) -> None:
        super().__init__(
            f"Path traversal rejected: '{requested}' is outside repo_path '{repo_path}'. "
            f"LaunchGuard read_file is repo-scoped (AI Operating Principles §5)."
        )
        self.code = "PATH_TRAVERSAL_VIOLATION"
        self.message = str(self)
        self.requested = requested
        self.repo_path = repo_path

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


# ---------------------------------------------------------------------------
# BE-01 Tool 1: parse_dockerfile
# ---------------------------------------------------------------------------

# Patterns for Dockerfile parsing
_RE_FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+\S+)?", re.IGNORECASE)
_RE_ENV = re.compile(r"^\s*ENV\s+(\w+)[=\s]+(\S+)", re.IGNORECASE)
_RE_EXPOSE = re.compile(r"^\s*EXPOSE\s+(\d+)", re.IGNORECASE)
_RE_CMD_EXEC = re.compile(r'^\s*CMD\s+\[', re.IGNORECASE)
_RE_CMD_SHELL = re.compile(r'^\s*CMD\s+(?!\[)', re.IGNORECASE)
_RE_ENTRYPOINT_EXEC = re.compile(r'^\s*ENTRYPOINT\s+\[', re.IGNORECASE)
_RE_USER = re.compile(r"^\s*USER\s+(\S+)", re.IGNORECASE)
_RE_SECRET_MOUNT = re.compile(r"--mount=type=secret,id=(\S+)", re.IGNORECASE)
_RE_ARG = re.compile(r"^\s*ARG\s+(\w+)", re.IGNORECASE)


def parse_dockerfile(repo_path: str, dockerfile_path: str = "Dockerfile") -> DockerfileFacts:
    """
    Parse a Dockerfile into DockerfileFacts.

    Extracts:
      - port + port_source (ENV PORT > EXPOSE; unknown if absent)
      - pid1_signal_safe (exec-form CMD/ENTRYPOINT = True; shell-form = False)
      - base_image_pinned (final/production stage: :tag or @sha256 = True; :latest or bare = False)
      - non_root_user (USER directive present and not "root")
      - secret_refs (--mount=type=secret refs)
      - env_vars (ENV directive names)
      - evidence (at least one per significant fact)

    Multi-stage Dockerfiles: judges the FINAL stage's FROM for base_image_pinned.

    Args:
        repo_path:       Absolute path to the repo root.
        dockerfile_path: Relative path within repo_path (default "Dockerfile").

    Returns:
        DockerfileFacts with all required fields populated.

    Raises:
        FileNotFoundError: if the Dockerfile does not exist at the resolved path.
    """
    full_path = Path(repo_path) / dockerfile_path
    if not full_path.exists():
        raise FileNotFoundError(f"Dockerfile not found: {full_path}")

    lines = full_path.read_text(encoding="utf-8").splitlines()

    # State variables
    port: int | None = None
    port_source: str = PortSource.UNKNOWN
    pid1_signal_safe: bool = False
    non_root_user: bool = False
    secret_refs: list[str] = []
    env_vars: list[str] = []
    evidence: list[Evidence] = []

    # Multi-stage tracking: we need the FINAL (production) FROM stage
    final_base_image: str | None = None
    # Shell-form CMD line for evidence
    unsafe_pid1_line: int | None = None
    has_exec_form: bool = False  # exec-form CMD or ENTRYPOINT found

    locator_prefix = dockerfile_path  # e.g. "Dockerfile" or "infra/Dockerfile"

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()

        # Skip comments and blank lines
        if not line or line.startswith("#"):
            continue

        # FROM — track all stages; final non-AS or final stage is the production stage
        m = _RE_FROM.match(line)
        if m:
            final_base_image = m.group(1)
            continue

        # ENV — extract PORT specially, record all others
        m = _RE_ENV.match(line)
        if m:
            var_name = m.group(1)
            var_value = m.group(2)
            env_vars.append(var_name)
            if var_name.upper() == "PORT":
                try:
                    port = int(var_value)
                    port_source = PortSource.DOCKERFILE_ENV
                    evidence.append(Evidence(
                        source=EvidenceSource.INTENDED,
                        locator=f"{locator_prefix}:{lineno}",
                        snippet=f"ENV PORT={var_value}",
                    ))
                except ValueError:
                    pass
            continue

        # EXPOSE — only use if no ENV PORT found yet
        m = _RE_EXPOSE.match(line)
        if m:
            expose_port = int(m.group(1))
            if port is None:
                port = expose_port
                port_source = PortSource.DOCKERFILE_EXPOSE
                evidence.append(Evidence(
                    source=EvidenceSource.INTENDED,
                    locator=f"{locator_prefix}:{lineno}",
                    snippet=f"EXPOSE {expose_port}",
                ))
            continue

        # CMD — exec-form vs shell-form
        if _RE_CMD_EXEC.match(line):
            has_exec_form = True
            evidence.append(Evidence(
                source=EvidenceSource.INTENDED,
                locator=f"{locator_prefix}:{lineno}",
                snippet=line[:120],
            ))
            continue

        if _RE_CMD_SHELL.match(line):
            # Shell-form CMD — PID 1 unsafe
            unsafe_pid1_line = lineno
            evidence.append(Evidence(
                source=EvidenceSource.INTENDED,
                locator=f"{locator_prefix}:{lineno}",
                snippet=line[:120],
            ))
            continue

        # ENTRYPOINT exec-form
        if _RE_ENTRYPOINT_EXEC.match(line):
            has_exec_form = True
            evidence.append(Evidence(
                source=EvidenceSource.INTENDED,
                locator=f"{locator_prefix}:{lineno}",
                snippet=line[:120],
            ))
            continue

        # USER directive
        m = _RE_USER.match(line)
        if m:
            user_val = m.group(1).lower()
            # non_root if USER present and not "root" / "0"
            if user_val not in ("root", "0"):
                non_root_user = True
                evidence.append(Evidence(
                    source=EvidenceSource.INTENDED,
                    locator=f"{locator_prefix}:{lineno}",
                    snippet=f"USER {m.group(1)}",
                ))
            continue

        # --mount=type=secret
        for sm in _RE_SECRET_MOUNT.finditer(line):
            secret_name = sm.group(1).rstrip(",")
            if secret_name not in secret_refs:
                secret_refs.append(secret_name)
                evidence.append(Evidence(
                    source=EvidenceSource.INTENDED,
                    locator=f"{locator_prefix}:{lineno}",
                    snippet=f"--mount=type=secret,id={secret_name}",
                ))

        # ARG — not tracked in env_vars (build-time only)

    # Determine pid1_signal_safe:
    # True if we found exec-form CMD or ENTRYPOINT; False if only shell-form found.
    # If neither, default True (no CMD = inherits from base, not our concern).
    if has_exec_form:
        pid1_signal_safe = True
    elif unsafe_pid1_line is not None:
        pid1_signal_safe = False
    else:
        # No CMD or ENTRYPOINT in this Dockerfile — inherit from base (conservative True)
        pid1_signal_safe = True

    # base_image_pinned: check the final FROM stage
    base_image_pinned = _is_image_pinned(final_base_image)
    if final_base_image:
        evidence.append(Evidence(
            source=EvidenceSource.INTENDED,
            locator=f"{locator_prefix}:1",
            snippet=f"FROM {final_base_image}",
        ))

    # Ensure at least one evidence entry
    if not evidence:
        evidence.append(Evidence(
            source=EvidenceSource.INTENDED,
            locator=f"{locator_prefix}:1",
            snippet="(parsed Dockerfile)",
        ))

    return DockerfileFacts(
        port=port,
        port_source=port_source,
        pid1_signal_safe=pid1_signal_safe,
        base_image_pinned=base_image_pinned,
        non_root_user=non_root_user,
        secret_refs=secret_refs,
        env_vars=env_vars,
        evidence=evidence,
    )


def _is_image_pinned(image_ref: str | None) -> bool:
    """
    Return True if the image reference includes a non-latest tag or SHA digest.

    Rules:
      - image:latest  → False
      - image         → False (no tag)
      - image:tag     → True  (pinned tag)
      - image@sha256: → True  (pinned digest)
      - scratch       → True  (special base)
    """
    if not image_ref:
        return False
    if image_ref.lower() == "scratch":
        return True
    if "@sha256:" in image_ref:
        return True
    if ":" in image_ref:
        _, tag = image_ref.rsplit(":", 1)
        return tag.lower() != "latest"
    # No tag at all (e.g. "python", "ubuntu") — not pinned
    return False


# ---------------------------------------------------------------------------
# BE-01 Tool 2: parse_app_entrypoint
# ---------------------------------------------------------------------------

# Patterns for detecting host binding in Python/Node/Go app files
_HOST_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    # uvicorn / fastapi — explicit host=
    (re.compile(r'uvicorn\.run\s*\([^)]*host\s*=\s*["\']0\.0\.0\.0["\']'), HostBinding.ALL, 1.0),
    (re.compile(r'uvicorn\.run\s*\([^)]*host\s*=\s*["\']localhost["\']'), HostBinding.LOCALHOST, 1.0),
    (re.compile(r'uvicorn\.run\s*\([^)]*host\s*=\s*["\']127\.0\.0\.1["\']'), HostBinding.LOOPBACK, 1.0),
    # --host flag in CLI invocations
    (re.compile(r'--host\s+0\.0\.0\.0'), HostBinding.ALL, 1.0),
    (re.compile(r'--host\s+localhost'), HostBinding.LOCALHOST, 1.0),
    (re.compile(r'--host\s+127\.0\.0\.1'), HostBinding.LOOPBACK, 1.0),
    # flask run --host
    (re.compile(r'app\.run\s*\([^)]*host\s*=\s*["\']0\.0\.0\.0["\']'), HostBinding.ALL, 1.0),
    (re.compile(r'app\.run\s*\([^)]*host\s*=\s*["\']127\.0\.0\.1["\']'), HostBinding.LOOPBACK, 1.0),
]

# Port extraction patterns
_PORT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'port\s*=\s*int\s*\(\s*os\.(?:environ|getenv)\s*\(\s*["\']PORT["\']'),
    re.compile(r'port\s*=\s*os\.(?:environ|getenv)\s*\(\s*["\']PORT["\']'),
    re.compile(r'--port\s+(\d+)'),
    re.compile(r'port\s*=\s*(\d+)'),
]

# Extensions to scan for entrypoint code
_APP_EXTENSIONS = {".py", ".js", ".ts", ".go", ".rb", ".sh"}

# Files to skip
_SKIP_DIRS = {"venv", ".venv", "node_modules", ".git", "__pycache__", ".pytest_cache"}


def parse_app_entrypoint(repo_path: str) -> EntrypointFacts:
    """
    Infer host/port binding from application code (BE-01 Tool 2).

    Scans Python/Node/Go source files for deterministic host-binding patterns.
    confidence=1.0 when a deterministic pattern is found.
    confidence<1.0 (and host_binding="unknown") when ambiguous — caller should
    escalate to LLM-02 (Gemini 2.5 Flash) for classification.

    DEFERRED: LLM-02 escalation path — this function sets host_binding=unknown +
    confidence<1.0 but does NOT call any model. The ADK RepoAuditor sub-agent
    handles the escalation (LLM-02 task).

    Args:
        repo_path: Absolute path to the repo root.

    Returns:
        EntrypointFacts with host_binding, port, confidence, evidence.
    """
    repo = Path(repo_path)
    evidence: list[Evidence] = []

    # Scan all app source files
    for root, dirs, files in os.walk(repo):
        # Prune directories to skip
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for fname in files:
            ext = Path(fname).suffix.lower()
            if ext not in _APP_EXTENSIONS:
                continue

            fpath = Path(root) / fname
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            rel_path = str(fpath.relative_to(repo))
            lines = content.splitlines()

            for lineno, line in enumerate(lines, start=1):
                for pattern, binding, conf in _HOST_PATTERNS:
                    if pattern.search(line):
                        evidence.append(Evidence(
                            source=EvidenceSource.INTENDED,
                            locator=f"{rel_path}:{lineno}",
                            snippet=line.strip()[:120],
                        ))
                        return EntrypointFacts(
                            host_binding=binding,
                            port=_extract_port_from_content(content),
                            confidence=conf,
                            evidence=evidence,
                        )

    # No deterministic match found — leave for LLM-02
    evidence.append(Evidence(
        source=EvidenceSource.INTENDED,
        locator=f"{repo_path}",
        snippet="(no deterministic host-binding pattern found; LLM-02 escalation needed)",
    ))
    return EntrypointFacts(
        host_binding=HostBinding.UNKNOWN,
        port=None,
        confidence=0.5,  # Ambiguous — below 1.0 signals escalation needed
        evidence=evidence,
    )


def _extract_port_from_content(content: str) -> int | None:
    """Extract a port number from app source content using pattern matching."""
    # Try os.environ.get("PORT") style first (dynamic — returns None for port)
    if re.search(r'os\.(?:environ|getenv)\s*\(\s*["\']PORT["\']', content):
        return None  # Dynamic — can't determine statically
    # Try explicit port=N
    for pattern in _PORT_PATTERNS:
        m = pattern.search(content)
        if m and m.lastindex:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                pass
    return None


# ---------------------------------------------------------------------------
# BE-01 Tool 3: read_file (repo-scoped, traversal-rejected)
# ---------------------------------------------------------------------------

def read_file(repo_path: str, file_path: str) -> dict[str, str]:
    """
    Read a single file scoped to repo_path.

    Rejects path traversal attempts — any resolved path outside repo_path raises
    PathTraversalError (maps to contract 403).

    This is a guardrail seam: the path is normalized and checked before any read.
    AI Operating Principles §5: repo content is DATA, never instruction.

    Args:
        repo_path: Absolute path to the repo root.
        file_path: Path relative to repo_path (may be absolute but must resolve inside).

    Returns:
        {"content": str} — file text content.

    Raises:
        PathTraversalError: if file_path resolves outside repo_path (403).
        FileNotFoundError: if the file does not exist.
    """
    repo = Path(repo_path).resolve()

    # Resolve the requested path (handles .., symlinks, etc.)
    # If file_path is absolute, Path(file_path) is used directly.
    # If relative, it is joined to repo_path.
    if Path(file_path).is_absolute():
        requested = Path(file_path).resolve()
    else:
        requested = (repo / file_path).resolve()

    # Enforce repo boundary
    try:
        requested.relative_to(repo)
    except ValueError:
        raise PathTraversalError(requested=str(requested), repo_path=str(repo))

    if not requested.exists():
        raise FileNotFoundError(f"File not found: {requested}")

    if not requested.is_file():
        raise ValueError(f"Path is not a file: {requested}")

    content = requested.read_text(encoding="utf-8", errors="replace")
    return {"content": content}


# ---------------------------------------------------------------------------
# BE-01 Tool 4: grep_code
# ---------------------------------------------------------------------------

def grep_code(repo_path: str, pattern: str) -> dict[str, list[dict[str, Any]]]:
    """
    Search repo files for a pattern and return REDACTED matches.

    Scans all text files under repo_path for lines matching `pattern` (Python regex).
    Secret values in matching lines are masked via redact() before return.

    AI Operating Principles §5: pattern input is DATA, never executed as code.
    The regex is compiled with re.IGNORECASE for broad coverage.

    Args:
        repo_path: Absolute path to the repo root.
        pattern:   Python regex pattern to search for.

    Returns:
        {"matches": [{"file": str, "line": int, "text": str}, ...]}
        Text is REDACTED (no secret values).
    """
    repo = Path(repo_path)
    matches: list[CodeMatch] = []

    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern '{pattern}': {e}") from e

    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for fname in files:
            fpath = Path(root) / fname
            # Skip binary files and very large files
            if fpath.stat().st_size > 1_000_000:  # 1MB limit
                continue
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue

            rel_path = str(fpath.relative_to(repo))

            for lineno, line in enumerate(content.splitlines(), start=1):
                if compiled.search(line):
                    # REDACT the line before returning (AI Operating Principles §3)
                    redacted_text = str(redact(line.strip()[:200]))
                    matches.append(CodeMatch(
                        file=rel_path,
                        line=lineno,
                        text=redacted_text,
                    ))

    return {"matches": [m.to_dict() for m in matches]}


# ---------------------------------------------------------------------------
# BE-01 Assembler: build_intended_contract
# ---------------------------------------------------------------------------

# Health probe route patterns
_HEALTH_PATTERNS = [
    re.compile(r'["\'/]health\b', re.IGNORECASE),
    re.compile(r'healthz', re.IGNORECASE),
    re.compile(r'health_check', re.IGNORECASE),
]

_STARTUP_PATTERNS = [
    re.compile(r'["\'/]ready\b', re.IGNORECASE),
    re.compile(r'readyz', re.IGNORECASE),
    re.compile(r'readiness', re.IGNORECASE),
    re.compile(r'startup', re.IGNORECASE),
]

# Patterns for secret refs in app code
_SECRET_ENV_PATTERNS = [
    re.compile(r'os\.environ\s*\[\s*["\']([A-Z][A-Z0-9_]{2,})["\']'),
    re.compile(r'os\.getenv\s*\(\s*["\']([A-Z][A-Z0-9_]{2,})["\']'),
    re.compile(r'os\.environ\.get\s*\(\s*["\']([A-Z][A-Z0-9_]{2,})["\']'),
]

# Known non-secret env vars (filter out standard ones from secret_refs)
_STANDARD_ENV_VARS: frozenset[str] = frozenset({
    "PORT", "HOST", "DEBUG", "ENV", "ENVIRONMENT", "LOG_LEVEL", "PATH",
    "HOME", "USER", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
    "NODE_ENV", "NODE_PATH",
})

# Patterns that suggest secret-like env vars (names, not values)
_SECRET_NAME_SUFFIXES = (
    "_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_PASSWD", "_CREDENTIAL",
    "_DSN", "_URI", "_URL", "_API_KEY",
)


def build_intended_contract(repo_path: str) -> IntendedContract:
    """
    Build an IntendedContract by assembling all repo parsers (BE-01 assembler).

    Runs:
      1. parse_dockerfile() — port, pid1, base_image, non_root, secret_refs, env_vars
      2. parse_app_entrypoint() — host_binding, confidence
      3. grep_code() for os.environ refs — additional secret_refs
      4. grep_code() for health/ready routes — expects_health_probe, expects_startup_probe

    All findings are merged into a single IntendedContract.

    Args:
        repo_path: Absolute path to the repo root.

    Returns:
        IntendedContract with all fields populated from deterministic analysis.
    """
    repo = Path(repo_path)

    # Step 1: Parse Dockerfile
    dockerfile_path = "Dockerfile"
    if not (repo / dockerfile_path).exists():
        # Try common locations
        for candidate in ["infra/Dockerfile", "docker/Dockerfile", "app/Dockerfile"]:
            if (repo / candidate).exists():
                dockerfile_path = candidate
                break

    try:
        df_facts = parse_dockerfile(repo_path, dockerfile_path)
    except FileNotFoundError:
        # No Dockerfile found — minimal facts
        df_facts = DockerfileFacts(
            port=None,
            port_source=PortSource.UNKNOWN,
            pid1_signal_safe=True,
            base_image_pinned=False,
            non_root_user=False,
            secret_refs=[],
            env_vars=[],
            evidence=[Evidence(
                source=EvidenceSource.INTENDED,
                locator=repo_path,
                snippet="(no Dockerfile found)",
            )],
        )

    # Step 2: Parse app entrypoint for host_binding
    ep_facts = parse_app_entrypoint(repo_path)

    # Step 3: Grep for os.environ refs to find additional secret refs
    env_secret_refs: list[str] = list(df_facts.secret_refs)
    try:
        grep_code(repo_path, r"os\.environ|os\.getenv|secretKeyRef")
        # We need env var NAMES from source, not from grep output (grep already redacted values).
        # Re-scan files for env var NAMES directly (names are safe to extract)
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = Path(root) / fname
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for pattern in _SECRET_ENV_PATTERNS:
                    for m in pattern.finditer(content):
                        var_name = m.group(1)
                        if (
                            var_name not in _STANDARD_ENV_VARS
                            and var_name not in env_secret_refs
                            and _looks_like_secret_name(var_name)
                        ):
                            env_secret_refs.append(var_name)
    except Exception:
        pass  # Best-effort; don't fail the contract build

    # Step 4: Detect health/readiness probe routes
    expects_health_probe = False
    expects_startup_probe = False
    try:
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fname in files:
                if not fname.endswith((".py", ".js", ".ts", ".go")):
                    continue
                fpath = Path(root) / fname
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for hp in _HEALTH_PATTERNS:
                    if hp.search(content):
                        expects_health_probe = True
                for sp in _STARTUP_PATTERNS:
                    if sp.search(content):
                        expects_startup_probe = True
    except Exception:
        pass  # Best-effort

    # Merge host_binding: entrypoint wins over default
    host_binding = ep_facts.host_binding

    return IntendedContract(
        port=df_facts.port,
        host_binding=host_binding,
        pid1_signal_safe=df_facts.pid1_signal_safe,
        base_image_pinned=df_facts.base_image_pinned,
        non_root_user=df_facts.non_root_user,
        secret_refs=sorted(set(env_secret_refs)),
        env_vars=df_facts.env_vars,
        expects_health_probe=expects_health_probe,
        expects_startup_probe=expects_startup_probe,
        required_apis=[],  # LLM-02 may populate this from code analysis (DEFERRED)
        confidence=ep_facts.confidence,
    )


def _looks_like_secret_name(var_name: str) -> bool:
    """Return True if a var name looks like it references a secret (not a config value)."""
    return any(var_name.endswith(suffix) for suffix in _SECRET_NAME_SUFFIXES)

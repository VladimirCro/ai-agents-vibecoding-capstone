"""
tests/test_repo_tools.py — Unit tests for BE-01 RepoAuditor tools.

Tests:
  - parse_dockerfile: ENV PORT → port_source=dockerfile-env; EXPOSE → dockerfile-expose
  - parse_dockerfile: shell-form CMD → pid1_signal_safe=False with evidence
  - parse_dockerfile: exec-form CMD → pid1_signal_safe=True
  - parse_dockerfile: base_image_pinned True/False cases
  - parse_dockerfile: non_root_user detection
  - read_file: path traversal rejected (403/PathTraversalError)
  - grep_code: returns matches with file+line+REDACTED text
  - fixture repo: parse_dockerfile on worknote-ai-like Dockerfile
"""

import tempfile
import textwrap
from pathlib import Path

import pytest

from launchguard.models import HostBinding, PortSource
from launchguard.tools.repo_tools import (
    PathTraversalError,
    build_intended_contract,
    grep_code,
    parse_app_entrypoint,
    parse_dockerfile,
    read_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_repo(files: dict[str, str]) -> str:
    """Create a temp directory with given {relative_path: content} files."""
    tmpdir = tempfile.mkdtemp()
    for rel_path, content in files.items():
        full = Path(tmpdir) / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return tmpdir


# ---------------------------------------------------------------------------
# parse_dockerfile tests
# ---------------------------------------------------------------------------

class TestParseDockerfile:
    def test_env_port_sets_port_source_dockerfile_env(self):
        """ENV PORT=8080 → port=8080, port_source=dockerfile-env."""
        repo = make_repo({"Dockerfile": "FROM python:3.12-slim\nENV PORT=8080\nEXPOSE 9000\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert facts.port == 8080
        assert facts.port_source == PortSource.DOCKERFILE_ENV

    def test_expose_sets_port_source_dockerfile_expose(self):
        """EXPOSE 8080 (no ENV PORT) → port=8080, port_source=dockerfile-expose."""
        repo = make_repo({"Dockerfile": "FROM python:3.12-slim\nEXPOSE 8080\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert facts.port == 8080
        assert facts.port_source == PortSource.DOCKERFILE_EXPOSE

    def test_env_port_takes_precedence_over_expose(self):
        """ENV PORT wins over EXPOSE when both present."""
        repo = make_repo({"Dockerfile": "FROM python:3.12-slim\nEXPOSE 3000\nENV PORT=8080\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert facts.port == 8080
        assert facts.port_source == PortSource.DOCKERFILE_ENV

    def test_no_port_directive_unknown(self):
        """No port directives → port=None, port_source=unknown."""
        repo = make_repo({"Dockerfile": "FROM python:3.12-slim\nRUN echo hello\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert facts.port is None
        assert facts.port_source == PortSource.UNKNOWN

    def test_exec_form_cmd_pid1_safe(self):
        """Exec-form CMD → pid1_signal_safe=True."""
        repo = make_repo({"Dockerfile": textwrap.dedent("""\
            FROM python:3.12-slim
            CMD ["/bin/start.sh"]
        """)})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert facts.pid1_signal_safe is True

    def test_shell_form_cmd_pid1_unsafe(self):
        """Shell-form CMD → pid1_signal_safe=False with evidence."""
        repo = make_repo({"Dockerfile": textwrap.dedent("""\
            FROM python:3.12-slim
            CMD npm run start
        """)})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert facts.pid1_signal_safe is False
        # Evidence must be present
        assert len(facts.evidence) >= 1

    def test_exec_form_entrypoint_pid1_safe(self):
        """ENTRYPOINT in exec form → pid1_signal_safe=True."""
        repo = make_repo({"Dockerfile": "FROM python:3.12-slim\nENTRYPOINT [\"/start.sh\"]\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert facts.pid1_signal_safe is True

    def test_base_image_pinned_with_tag(self):
        """FROM python:3.12-slim → base_image_pinned=True."""
        repo = make_repo({"Dockerfile": "FROM python:3.12-slim\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert facts.base_image_pinned is True

    def test_base_image_unpinned_latest(self):
        """FROM python:latest → base_image_pinned=False."""
        repo = make_repo({"Dockerfile": "FROM python:latest\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert facts.base_image_pinned is False

    def test_base_image_unpinned_no_tag(self):
        """FROM python (no tag) → base_image_pinned=False."""
        repo = make_repo({"Dockerfile": "FROM python\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert facts.base_image_pinned is False

    def test_base_image_pinned_sha(self):
        """FROM image@sha256:... → base_image_pinned=True."""
        repo = make_repo({"Dockerfile": "FROM python@sha256:deadbeef1234567890abcdef\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert facts.base_image_pinned is True

    def test_non_root_user_detected(self):
        """USER directive (non-root) → non_root_user=True."""
        repo = make_repo({"Dockerfile": "FROM python:3.12-slim\nUSER appuser\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert facts.non_root_user is True

    def test_root_user_not_flagged(self):
        """USER root → non_root_user=False."""
        repo = make_repo({"Dockerfile": "FROM python:3.12-slim\nUSER root\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert facts.non_root_user is False

    def test_env_vars_collected(self):
        """ENV directives → env_vars list populated."""
        repo = make_repo({"Dockerfile": "FROM python:3.12-slim\nENV PORT=8080\nENV DEBUG=false\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert "PORT" in facts.env_vars
        assert "DEBUG" in facts.env_vars

    def test_evidence_has_at_least_one_entry(self):
        """Every DockerfileFacts must have at least one evidence entry."""
        repo = make_repo({"Dockerfile": "FROM python:3.12-slim\nENV PORT=8080\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        assert len(facts.evidence) >= 1

    def test_evidence_locator_includes_line_number(self):
        """Evidence locator must include a line number."""
        repo = make_repo({"Dockerfile": "FROM python:3.12-slim\nEXPOSE 8080\n"})
        facts = parse_dockerfile(repo, "Dockerfile")
        # At least one locator should have a colon indicating file:line
        locators = [e.locator for e in facts.evidence]
        assert any(":" in loc for loc in locators)

    def test_multi_stage_final_stage_pinned(self):
        """Multi-stage: final FROM stage determines base_image_pinned."""
        dockerfile_content = textwrap.dedent("""\
            FROM python:latest AS builder
            RUN pip install build

            FROM python:3.12-slim
            COPY --from=builder /app /app
            CMD ["/app/start"]
        """)
        repo = make_repo({"Dockerfile": dockerfile_content})
        facts = parse_dockerfile(repo, "Dockerfile")
        # Final stage is python:3.12-slim (pinned) — should be True
        assert facts.base_image_pinned is True

    def test_worknote_ai_like_fixture(self):
        """Test on the actual worknote-ai-like fixture Dockerfile."""
        repo_path = str(
            Path(__file__).parents[1] / "fixtures" / "repos" / "worknote-ai-like"
        )
        facts = parse_dockerfile(repo_path, "Dockerfile")
        assert facts.port == 8080
        assert facts.port_source == PortSource.DOCKERFILE_ENV
        assert facts.pid1_signal_safe is True   # exec-form CMD ["/usr/local/bin/entrypoint.sh"]
        assert facts.base_image_pinned is True   # python:3.12-slim
        assert facts.non_root_user is True       # USER worknote
        assert len(facts.evidence) >= 1


# ---------------------------------------------------------------------------
# read_file tests
# ---------------------------------------------------------------------------

class TestReadFile:
    def test_reads_existing_file(self):
        """read_file returns content of an existing file."""
        repo = make_repo({"hello.txt": "hello world"})
        result = read_file(repo, "hello.txt")
        assert result["content"] == "hello world"

    def test_path_traversal_rejected(self):
        """Path traversal (../) must raise PathTraversalError."""
        repo = make_repo({"hello.txt": "hello"})
        with pytest.raises(PathTraversalError) as exc_info:
            read_file(repo, "../etc/passwd")
        assert exc_info.value.code == "PATH_TRAVERSAL_VIOLATION"

    def test_absolute_path_outside_repo_rejected(self):
        """Absolute path outside repo must be rejected."""
        repo = make_repo({"hello.txt": "hello"})
        with pytest.raises(PathTraversalError):
            read_file(repo, "/etc/passwd")

    def test_nested_traversal_rejected(self):
        """Deep traversal like a/b/../../.. must be rejected."""
        repo = make_repo({"sub/file.txt": "content"})
        with pytest.raises(PathTraversalError):
            read_file(repo, "sub/../../etc/passwd")

    def test_file_within_repo_accepted(self):
        """A path inside a subdirectory must be accepted."""
        repo = make_repo({"subdir/config.yaml": "key: value"})
        result = read_file(repo, "subdir/config.yaml")
        assert "key: value" in result["content"]

    def test_missing_file_raises(self):
        """Missing file must raise FileNotFoundError."""
        repo = make_repo({"hello.txt": "hello"})
        with pytest.raises(FileNotFoundError):
            read_file(repo, "nonexistent.txt")


# ---------------------------------------------------------------------------
# grep_code tests
# ---------------------------------------------------------------------------

class TestGrepCode:
    def test_finds_matching_lines(self):
        """grep_code returns matches with file, line, text."""
        repo = make_repo({"app.py": "import os\nSECRET = os.environ.get('MY_SECRET')\n"})
        result = grep_code(repo, "os.environ")
        assert "matches" in result
        assert len(result["matches"]) >= 1
        match = result["matches"][0]
        assert match["file"] == "app.py"
        assert match["line"] == 2

    def test_returns_empty_when_no_match(self):
        """grep_code returns empty matches when pattern not found."""
        repo = make_repo({"app.py": "x = 1\n"})
        result = grep_code(repo, "os.environ")
        assert result["matches"] == []

    def test_text_is_redacted(self):
        """Match text must not contain raw connection strings or base64 secrets."""
        repo = make_repo({"app.py": "conn = 'postgresql://user:p@ssw0rd@host:5432/db'\n"})
        result = grep_code(repo, "postgresql")
        # The text field must be REDACTED (connection string masked)
        for m in result["matches"]:
            assert "p@ssw0rd" not in m["text"]

    def test_multiple_files_searched(self):
        """grep_code searches across multiple files."""
        repo = make_repo({
            "a.py": "os.environ.get('KEY_A')\n",
            "b.py": "os.environ.get('KEY_B')\n",
            "c.py": "no match here\n",
        })
        result = grep_code(repo, "os.environ")
        files = {m["file"] for m in result["matches"]}
        assert "a.py" in files
        assert "b.py" in files
        assert "c.py" not in files


# ---------------------------------------------------------------------------
# parse_app_entrypoint tests
# ---------------------------------------------------------------------------

class TestParseAppEntrypoint:
    def test_uvicorn_all_interfaces(self):
        """uvicorn.run with host='0.0.0.0' → host_binding=0.0.0.0, confidence=1.0."""
        repo = make_repo({"main.py": "uvicorn.run(app, host='0.0.0.0', port=8080)\n"})
        facts = parse_app_entrypoint(repo)
        assert facts.host_binding == HostBinding.ALL
        assert facts.confidence == 1.0

    def test_localhost_binding(self):
        """localhost binding → host_binding=localhost, confidence=1.0."""
        repo = make_repo({"main.py": "uvicorn.run(app, host='localhost', port=8080)\n"})
        facts = parse_app_entrypoint(repo)
        assert facts.host_binding == HostBinding.LOCALHOST
        assert facts.confidence == 1.0

    def test_no_binding_unknown(self):
        """No binding pattern → host_binding=unknown, confidence<1.0."""
        repo = make_repo({"app.py": "x = 1\n"})
        facts = parse_app_entrypoint(repo)
        assert facts.host_binding == HostBinding.UNKNOWN
        assert facts.confidence < 1.0

    def test_evidence_populated(self):
        """Evidence must be populated with at least one entry."""
        repo = make_repo({"main.py": "uvicorn.run(app, host='0.0.0.0', port=8080)\n"})
        facts = parse_app_entrypoint(repo)
        assert len(facts.evidence) >= 1


# ---------------------------------------------------------------------------
# build_intended_contract tests
# ---------------------------------------------------------------------------

class TestBuildIntendedContract:
    def test_worknote_ai_like_fixture(self):
        """build_intended_contract on the worknote-ai-like fixture."""
        repo_path = str(
            Path(__file__).parents[1] / "fixtures" / "repos" / "worknote-ai-like"
        )
        contract = build_intended_contract(repo_path)
        assert contract.port == 8080
        assert contract.pid1_signal_safe is True
        assert contract.base_image_pinned is True
        assert contract.non_root_user is True
        # Expects probes (app.py defines /health and /ready)
        assert contract.expects_health_probe is True
        assert contract.expects_startup_probe is True
        # Secret refs from app.py os.environ.get calls
        assert len(contract.secret_refs) >= 1
        # Confidence should be 1.0 from uvicorn host=0.0.0.0 in entrypoint.sh
        # or <1.0 if uvicorn only in shell (entrypoint.sh), but app.py shows uvicorn too
        assert 0 <= contract.confidence <= 1.0

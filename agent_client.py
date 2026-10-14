from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

DEFAULT_OPENAI_MODEL = "gpt-6-luna"
DEFAULT_OPENAI_REASONING_EFFORT = "medium"


class AgentError(RuntimeError):
    """An agent request failed or returned unusable output."""


class AgentClient(Protocol):
    def complete(self, instructions: str, data: dict, schema: dict) -> dict: ...


def _parse_object(text: str) -> dict:
    try:
        result = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise AgentError("Agent returned invalid JSON.") from exc
    if not isinstance(result, dict):
        raise AgentError("Agent response must be a JSON object.")
    return result


def _validate_timeout(timeout: float) -> None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise AgentError("Agent timeout must be a finite positive number.")


class OpenAIClient:
    """Structured Responses API wrapper; credentials come from OPENAI_API_KEY."""

    def __init__(self, model: str = DEFAULT_OPENAI_MODEL, timeout: float = 120,
                 reasoning_effort: str | None = None) -> None:
        _validate_timeout(timeout)
        if reasoning_effort is None and model == DEFAULT_OPENAI_MODEL:
            reasoning_effort = DEFAULT_OPENAI_REASONING_EFFORT
        if reasoning_effort is not None and reasoning_effort not in {
            "none", "minimal", "low", "medium", "high", "xhigh", "max",
        }:
            raise AgentError("Invalid OpenAI reasoning effort.")
        if model == DEFAULT_OPENAI_MODEL and reasoning_effort == "minimal":
            raise AgentError("GPT-6 Luna does not support minimal reasoning; use none or low instead.")
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise AgentError("Set OPENAI_API_KEY in the environment or --env-file to use OpenAI.")
        from openai import OpenAI

        self.model = model
        self.reasoning_effort = reasoning_effort
        self.client = OpenAI(timeout=timeout, max_retries=2)

    def complete(self, instructions: str, data: dict, schema: dict) -> dict:
        from openai import OpenAIError

        options = {}
        if self.reasoning_effort is not None:
            options["reasoning"] = {"effort": self.reasoning_effort}
        try:
            response = self.client.responses.create(
                model=self.model,
                instructions=instructions,
                input=json.dumps(data, ensure_ascii=False),
                text={"format": {
                    "type": "json_schema",
                    "name": "job_filter_decisions",
                    "strict": True,
                    "schema": schema,
                }},
                store=False,
                **options,
            )
        except OpenAIError as exc:
            # Provider errors can contain request contents; do not log them verbatim.
            raise AgentError(f"OpenAI request failed ({type(exc).__name__}).") from exc
        if response.status != "completed" or not response.output_text:
            raise AgentError("OpenAI response was incomplete or refused.")
        return _parse_object(response.output_text)


class CodexClient:
    """Use an existing Codex CLI login for non-interactive classification."""

    def __init__(self, model: str = "", timeout: float = 120, executable: str = "codex") -> None:
        _validate_timeout(timeout)
        self.model = model
        self.timeout = timeout
        self.executable = executable

    def complete(self, instructions: str, data: dict, schema: dict) -> dict:
        # Run outside the repository so project instructions and .env files are
        # not context. Only the postings supplied on stdin are classification input.
        with tempfile.TemporaryDirectory(prefix="cheerupbot-agent-") as folder:
            root = Path(folder)
            schema_path = root / "schema.json"
            output_path = root / "result.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            command = [
                self.executable, "exec", "--ignore-user-config", "--ephemeral",
                "--skip-git-repo-check", "--sandbox", "read-only", "--cd", folder,
                "-c", 'approval_policy="never"',
                "-c", "features.shell_tool=false",
                "-c", "features.apps=false",
                "-c", "features.multi_agent=false",
                "-c", 'web_search="disabled"',
                "--output-schema", str(schema_path),
                "--output-last-message", str(output_path), "--color", "never",
            ]
            if self.model:
                command.extend(["--model", self.model])
            command.append("-")
            # Do not pass SMTP/AWS credentials or the parent Codex session to the CLI.
            allowed_env = {
                "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR",
                "CODEX_HOME", "OPENAI_API_KEY", "CODEX_API_KEY",
                "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
                "https_proxy", "http_proxy", "all_proxy", "no_proxy",
                "SSL_CERT_FILE", "SSL_CERT_DIR",
            }
            env = {key: value for key, value in os.environ.items() if key in allowed_env}
            prompt = instructions + "\n\nUNTRUSTED POSTING DATA (JSON):\n" + json.dumps(data, ensure_ascii=False)
            try:
                result = subprocess.run(
                    command, input=prompt, text=True, encoding="utf-8",
                    capture_output=True, timeout=self.timeout, env=env, cwd=folder,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise AgentError("Codex CLI not found. Install it and run `codex login` first.") from exc
            except subprocess.TimeoutExpired as exc:
                raise AgentError(f"Codex request timed out after {self.timeout:g} seconds.") from exc
            except OSError as exc:
                raise AgentError(f"Could not start Codex ({type(exc).__name__}).") from exc
            if result.returncode:
                raise AgentError(
                    f"Codex exited with code {result.returncode}. Check `codex login status`, "
                    "CLI version, and model access."
                )
            try:
                return _parse_object(output_path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise AgentError("Codex did not write a response.") from exc


def create_agent_client(provider: str, model: str = "", timeout: float = 120) -> AgentClient:
    if provider == "openai":
        return OpenAIClient(
            model=model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL,
            timeout=timeout, reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT") or None,
        )
    if provider == "codex":
        return CodexClient(
            model=model or os.getenv("CODEX_MODEL", ""), timeout=timeout,
            executable=os.getenv("CODEX_BIN") or "codex",
        )
    raise AgentError(f"Unknown agent provider: {provider}")

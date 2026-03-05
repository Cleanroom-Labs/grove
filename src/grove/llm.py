"""
grove.llm
Optional commit/squash message generation helpers.
"""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from typing import Any

from grove.config import LLMConfig, LLMProviderEntry, load_config
from grove.repo_utils import Colors, run_git

COMMIT_PROMPT = """Write a commit message for the staged changes below.

<format>
- Subject line under 50 chars
- For material changes, add a blank line then a body paragraph explaining the change
- Output only the commit message, no quotes or code blocks
</format>

<style>
- Imperative mood: "Add feature" not "Added feature"
- Match recent commit style (conventional commits if used)
- Describe the change, not the intent or benefit
</style>

<diffstat>{diffstat}</diffstat>
<diff>{diff}</diff>
<context>
Branch: {branch}
<recent_commits>{recent_commits}</recent_commits>
</context>
"""

SQUASH_PROMPT = """Combine these commits into a single commit message.

<format>
- Subject line under 50 chars
- For material changes, add a blank line then a body paragraph explaining the change
- Output only the commit message, no quotes or code blocks
</format>

<style>
- Imperative mood: "Add feature" not "Added feature"
- Match the style of commits being squashed (conventional commits if used)
- Describe the change, not the intent or benefit
</style>

<commits branch="{branch}" target="{target}">{commits}</commits>
<diffstat>{diffstat}</diffstat>
<diff>{diff}</diff>
"""

_DIFFSTAT_MAX_CHARS = 4_000
_DIFF_MAX_CHARS = 20_000
_RECENT_MAX_CHARS = 4_000
_COMMITS_MAX_CHARS = 8_000

_PROVIDER_MODULES = {
    "anthropic": "strands.models.anthropic",
    "ollama": "strands.models.ollama",
    "openai": "strands.models.openai",
    "litellm": "strands.models.litellm",
}

_PROVIDER_CLASS_CANDIDATES = {
    "anthropic": ("AnthropicModel", "Model"),
    "ollama": ("OllamaModel", "Model"),
    "openai": ("OpenAIModel", "Model"),
    "litellm": ("LiteLLMModel", "Model"),
}


class LLMUnavailableError(RuntimeError):
    """Raised when optional LLM dependencies are unavailable."""


def _truncate(text: str, max_chars: int) -> str:
    """Truncate large prompt sections to keep generation bounded."""
    if len(text) <= max_chars:
        return text
    marker = "\n...[truncated]...\n"
    keep = max_chars - len(marker)
    if keep <= 0:
        return marker.strip()
    return text[:keep] + marker


def _current_branch(repo_root: Path) -> str:
    result = run_git(repo_root, "branch", "--show-current", check=False)
    branch = result.stdout.strip()
    return branch or "HEAD"


def build_commit_prompt(repo_root: Path) -> str:
    """Build a structured commit prompt from staged changes."""
    diffstat_result = run_git(repo_root, "diff", "--cached", "--stat", check=False)
    diff_result = run_git(repo_root, "diff", "--cached", check=False)
    recent_result = run_git(
        repo_root,
        "log",
        "-5",
        "--pretty=format:%h %s",
        check=False,
    )

    diffstat = diffstat_result.stdout.strip() if diffstat_result.returncode == 0 else ""
    diff = diff_result.stdout.strip() if diff_result.returncode == 0 else ""
    recent = recent_result.stdout.strip() if recent_result.returncode == 0 else ""

    return COMMIT_PROMPT.format(
        diffstat=_truncate(diffstat, _DIFFSTAT_MAX_CHARS),
        diff=_truncate(diff, _DIFF_MAX_CHARS),
        branch=_current_branch(repo_root),
        recent_commits=_truncate(recent, _RECENT_MAX_CHARS),
    )


def build_squash_prompt(repo_root: Path, base: str, target: str) -> str:
    """Build a structured squash prompt from commits since base."""
    commits_result = run_git(
        repo_root,
        "log",
        "--reverse",
        "--format=%h %s",
        f"{base}..HEAD",
        check=False,
    )
    diffstat_result = run_git(repo_root, "diff", "--stat", base, "HEAD", check=False)
    diff_result = run_git(repo_root, "diff", base, "HEAD", check=False)

    commits = commits_result.stdout.strip() if commits_result.returncode == 0 else ""
    diffstat = diffstat_result.stdout.strip() if diffstat_result.returncode == 0 else ""
    diff = diff_result.stdout.strip() if diff_result.returncode == 0 else ""

    return SQUASH_PROMPT.format(
        branch=_current_branch(repo_root),
        target=target,
        commits=_truncate(commits, _COMMITS_MAX_CHARS),
        diffstat=_truncate(diffstat, _DIFFSTAT_MAX_CHARS),
        diff=_truncate(diff, _DIFF_MAX_CHARS),
    )


def _generation_command(repo_root: Path) -> str | None:
    """Resolve commit generation command from Grove config."""
    try:
        command = load_config(repo_root).commit.generation.command
    except ValueError as exc:
        print(
            f"{Colors.yellow('Warning')}: failed to load config for message "
            f"generation ({exc}); falling back to editor."
        )
        return None

    if not command or not command.strip():
        return None

    return command


def _run_generation_command(repo_root: Path, command: str, prompt: str) -> str | None:
    """Run shell-command generation and return message text."""
    result = subprocess.run(
        command,
        shell=True,
        cwd=str(repo_root),
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print(
            f"{Colors.yellow('Warning')}: message generation command failed "
            f"(exit {result.returncode}); falling back."
        )
        return None

    message = result.stdout.strip()
    if not message:
        print(
            f"{Colors.yellow('Warning')}: message generation returned empty output; "
            "falling back."
        )
        return None
    return message


def _load_strands_runtime() -> Any:
    """Load strands runtime lazily."""
    try:
        return importlib.import_module("strands")
    except ModuleNotFoundError as exc:
        raise LLMUnavailableError(
            "Strands is not installed. Install optional dependencies with "
            "`pip install grove[llm]`."
        ) from exc


def _build_model(provider_entry: LLMProviderEntry) -> Any:
    """Instantiate a provider model for strands execution."""
    module_name = _PROVIDER_MODULES[provider_entry.provider]
    module = importlib.import_module(module_name)

    model_cls = None
    for class_name in _PROVIDER_CLASS_CANDIDATES[provider_entry.provider]:
        model_cls = getattr(module, class_name, None)
        if model_cls is not None:
            break
    if model_cls is None:
        raise RuntimeError(
            f"Could not find model class for provider {provider_entry.provider!r} "
            f"in {module_name}"
        )

    try:
        return model_cls(model=provider_entry.model)
    except TypeError:
        return model_cls(provider_entry.model)


def _extract_text(response: Any) -> str:
    """Extract text from varied provider response shapes."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response.strip()
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text.strip()
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            item_text = getattr(item, "text", None)
            if isinstance(item_text, str):
                parts.append(item_text)
        return "\n".join(part.strip() for part in parts if part.strip()).strip()
    return str(response).strip()


def _run_strands_prompt(strands_runtime: Any, model: Any, prompt: str) -> str | None:
    """Execute a prompt through strands agent/model and return output text."""
    agent_cls = getattr(strands_runtime, "Agent", None)
    if agent_cls is not None:
        response = agent_cls(model=model)(prompt)
        text = _extract_text(response)
        return text or None

    if callable(model):
        text = _extract_text(model(prompt))
        return text or None

    for method_name in ("generate", "complete"):
        method = getattr(model, method_name, None)
        if method is None:
            continue
        text = _extract_text(method(prompt))
        return text or None

    raise RuntimeError("Model is not callable and exposes no generate/complete method")


def _try_strands_providers(prompt: str, llm_config: LLMConfig) -> str | None:
    """Try configured strands providers in order."""
    strands_runtime = _load_strands_runtime()

    for provider_entry in llm_config.providers:
        try:
            model = _build_model(provider_entry)
            generated = _run_strands_prompt(strands_runtime, model, prompt)
            if generated:
                return generated
            print(
                f"{Colors.yellow('Warning')}: provider "
                f"{provider_entry.provider}:{provider_entry.model} returned no text; "
                "trying next provider."
            )
        except Exception as exc:
            print(
                f"{Colors.yellow('Warning')}: provider "
                f"{provider_entry.provider}:{provider_entry.model} failed ({exc}); "
                "trying next provider."
            )

    return None


def generate_message(repo_root: Path, prompt: str) -> str | None:
    """Generate commit/squash message using shell command then optional providers."""
    command = _generation_command(repo_root)
    if command:
        message = _run_generation_command(repo_root, command, prompt)
        if message:
            return message

    try:
        llm_config = load_config(repo_root).worktree.llm
    except ValueError:
        llm_config = LLMConfig()

    if llm_config.providers:
        try:
            message = _try_strands_providers(prompt, llm_config)
        except LLMUnavailableError as exc:
            print(f"{Colors.yellow('Warning')}: {exc}")
            return None
        if message:
            return message

    return None

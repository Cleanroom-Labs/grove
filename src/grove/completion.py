"""
grove.completion
Generate shell completion scripts by introspecting the argparse parser.
"""

from __future__ import annotations

import argparse
from pathlib import Path


# ---------------------------------------------------------------------------
# Parser introspection
# ---------------------------------------------------------------------------

def _get_subparsers_action(parser: argparse.ArgumentParser):
    """Return the _SubParsersAction from a parser, or None."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _get_flags(parser: argparse.ArgumentParser) -> list[str]:
    """Return all option strings (flags) for a parser, excluding -h/--help."""
    flags: list[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        for opt in action.option_strings:
            flags.append(opt)
    return flags


def _get_positional_choices(parser: argparse.ArgumentParser) -> list[str]:
    """Return choice values from positional arguments (e.g. shell={bash,zsh,fish})."""
    choices: list[str] = []
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        if not action.option_strings and action.choices:
            choices.extend(action.choices)
    return choices


def extract_structure(parser: argparse.ArgumentParser) -> dict:
    """Extract the full command structure from an argparse parser.

    Returns a nested dict with flags, positional choices, and subcommands
    at each level of the command hierarchy.
    """
    result: dict = {
        "flags": _get_flags(parser),
        "choices": _get_positional_choices(parser),
        "commands": {},
    }
    sub_action = _get_subparsers_action(parser)
    if sub_action:
        for name, subparser in sub_action.choices.items():
            result["commands"][name] = extract_structure(subparser)
    return result


# ---------------------------------------------------------------------------
# Collect all subcommand names (used by bash/zsh word-walking)
# ---------------------------------------------------------------------------

def _collect_all_subcommands(structure: dict) -> set[str]:
    """Recursively collect every subcommand name in the tree."""
    names: set[str] = set()
    for cmd, sub in structure["commands"].items():
        names.add(cmd)
        names.update(_collect_all_subcommands(sub))
    return names


# ---------------------------------------------------------------------------
# Bash completion
# ---------------------------------------------------------------------------

def _generate_bash(structure: dict) -> str:
    """Generate a bash completion script from the extracted parser structure."""
    all_subcmds = sorted(_collect_all_subcommands(structure))
    lines: list[str] = []

    lines.append("# grove shell completion (bash)")
    lines.append("# eval \"$(grove completion bash)\"")
    lines.append("")
    lines.append("_grove_completion() {")
    lines.append("    local cur prev words cword")
    lines.append("    if type _init_completion &>/dev/null; then")
    lines.append("        _init_completion || return")
    lines.append("    else")
    lines.append("        COMPREPLY=()")
    lines.append('        cur="${COMP_WORDS[COMP_CWORD]}"')
    lines.append('        words=("${COMP_WORDS[@]}")')
    lines.append("        cword=$COMP_CWORD")
    lines.append("    fi")
    lines.append("")
    lines.append("    # Walk COMP_WORDS to find the deepest subcommand")
    lines.append('    local cmd_path="grove"')
    lines.append("    local i=1")
    lines.append("    while [[ $i -lt $cword ]]; do")
    lines.append('        local word="${words[$i]}"')
    lines.append('        case "$word" in')
    lines.append("            -*) ;;  # skip flags")
    lines.append("            *)")
    lines.append('                case "$word" in')
    lines.append(f'                    {" | ".join(all_subcmds)})')
    lines.append('                        cmd_path="${cmd_path}_${word}"')
    lines.append("                        ;;")
    lines.append("                esac")
    lines.append("                ;;")
    lines.append("        esac")
    lines.append("        (( i++ ))")
    lines.append("    done")
    lines.append("")
    lines.append('    case "$cmd_path" in')

    def _emit(struct: dict, path: str) -> None:
        subcmds = sorted(struct["commands"].keys())
        flags = struct["flags"]
        choices = struct.get("choices", [])
        completions = subcmds + choices + flags
        if completions:
            lines.append(f"        {path})")
            words_str = " ".join(completions)
            lines.append(f"            COMPREPLY=($(compgen -W '{words_str}' -- \"$cur\"))")
            lines.append("            return ;;")
        for cmd in subcmds:
            _emit(struct["commands"][cmd], f"{path}_{cmd}")

    _emit(structure, "grove")

    lines.append("    esac")
    lines.append("}")
    lines.append("")
    lines.append("complete -o default -F _grove_completion grove")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Zsh completion
# ---------------------------------------------------------------------------

def _generate_zsh(structure: dict) -> str:
    """Generate a zsh completion script from the extracted parser structure."""
    all_subcmds = sorted(_collect_all_subcommands(structure))
    lines: list[str] = []

    lines.append("#compdef grove")
    lines.append("# grove shell completion (zsh)")
    lines.append("# eval \"$(grove completion zsh)\"")
    lines.append("")
    lines.append("_grove() {")
    lines.append("")
    lines.append("    # Walk words to find deepest subcommand")
    lines.append('    local cmd_path="grove"')
    lines.append("    local i=1")
    lines.append("    while [[ $i -lt $CURRENT ]]; do")
    lines.append('        local word="${words[$((i+1))]}"')  # zsh words is 1-indexed
    lines.append('        case "$word" in')
    lines.append("            -*) ;;")
    lines.append("            *)")
    lines.append('                case "$word" in')
    lines.append(f'                    {" | ".join(all_subcmds)})')
    lines.append('                        cmd_path="${cmd_path}_${word}"')
    lines.append("                        ;;")
    lines.append("                esac")
    lines.append("                ;;")
    lines.append("        esac")
    lines.append("        (( i++ ))")
    lines.append("    done")
    lines.append("")
    lines.append('    case "$cmd_path" in')

    def _emit(struct: dict, path: str) -> None:
        subcmds = sorted(struct["commands"].keys())
        flags = struct["flags"]
        choices = struct.get("choices", [])
        completions = subcmds + choices + flags
        if completions:
            words_str = " ".join(completions)
            lines.append(f"        {path})")
            lines.append(f"            compadd -- {words_str}")
            lines.append("            ;;")
        for cmd in subcmds:
            _emit(struct["commands"][cmd], f"{path}_{cmd}")

    _emit(structure, "grove")

    lines.append("    esac")
    lines.append("}")
    lines.append("")
    lines.append("compdef _grove grove")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fish completion
# ---------------------------------------------------------------------------

def _generate_fish(structure: dict) -> str:
    """Generate a fish completion script from the extracted parser structure."""
    lines: list[str] = []
    lines.append("# grove shell completion (fish)")
    lines.append("# grove completion fish | source")
    lines.append("")

    def _emit(struct: dict, path_parts: list[str]) -> None:
        subcmds = sorted(struct["commands"].keys())
        flags = struct["flags"]
        choices = struct.get("choices", [])

        # Build the condition for this command level
        if not path_parts:
            cond = "__fish_use_subcommand"
        else:
            parts = [f"__fish_seen_subcommand_from {path_parts[-1]}"]
            if subcmds:
                parts.append(
                    f"not __fish_seen_subcommand_from {' '.join(subcmds)}"
                )
            cond = "; and ".join(parts)

        for cmd in subcmds:
            lines.append(f"complete -c grove -f -n '{cond}' -a '{cmd}'")

        for choice in choices:
            lines.append(f"complete -c grove -f -n '{cond}' -a '{choice}'")

        for flag in flags:
            if flag.startswith("--"):
                long_name = flag[2:]
                lines.append(f"complete -c grove -f -n '{cond}' -l '{long_name}'")
            elif flag.startswith("-") and len(flag) == 2:
                lines.append(f"complete -c grove -f -n '{cond}' -s '{flag[1]}'")

        for cmd in subcmds:
            _emit(struct["commands"][cmd], path_parts + [cmd])

    _emit(structure, [])

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_GENERATORS = {
    "bash": _generate_bash,
    "zsh": _generate_zsh,
    "fish": _generate_fish,
}


def run(args) -> int:
    """Generate and print a shell completion script."""
    from grove.cli import build_parser

    parser = build_parser()
    structure = extract_structure(parser)
    script = _GENERATORS[args.completion_command](structure)
    print(script)
    return 0


# ---------------------------------------------------------------------------
# Completion install — sentinel markers
# ---------------------------------------------------------------------------

_SENTINEL_BEGIN = "# >>> grove completion >>>"
_SENTINEL_END = "# <<< grove completion <<<"


# ---------------------------------------------------------------------------
# Shell / profile detection
# ---------------------------------------------------------------------------

def _detect_shell() -> str | None:
    """Detect the current shell from $SHELL."""
    import os

    shell_path = os.environ.get("SHELL", "")
    basename = Path(shell_path).name if shell_path else ""
    if basename in ("bash", "zsh", "fish"):
        return basename
    return None


def _get_profile_path(shell: str) -> Path | None:
    """Return the profile file path for the given shell, or None for fish."""
    home = Path.home()
    if shell == "bash":
        bashrc = home / ".bashrc"
        bash_profile = home / ".bash_profile"
        if bashrc.exists():
            return bashrc
        if bash_profile.exists():
            return bash_profile
        return bashrc  # default when neither exists
    if shell == "zsh":
        return home / ".zshrc"
    return None  # fish uses a completions file, not a profile line


def _get_fish_completions_path() -> Path:
    """Return the fish completions file path."""
    return Path.home() / ".config" / "fish" / "completions" / "grove.fish"


# ---------------------------------------------------------------------------
# Profile file manipulation
# ---------------------------------------------------------------------------

def _build_profile_block(script: str) -> str:
    """Wrap a completion script in sentinel markers for profile injection."""
    return (
        f"{_SENTINEL_BEGIN}\n"
        f"{script}\n"
        f"{_SENTINEL_END}\n"
    )


def _has_grove_block(content: str) -> bool:
    """Check if the grove sentinel markers are present in file content."""
    return _SENTINEL_BEGIN in content and _SENTINEL_END in content


def _inject_block(content: str, block: str) -> str:
    """Append the grove block to file content with a blank-line separator."""
    if content and not content.endswith("\n"):
        content += "\n"
    if content and not content.endswith("\n\n"):
        content += "\n"
    return content + block


def _replace_block(content: str, block: str) -> str:
    """Replace an existing grove sentinel block in file content."""
    import re

    pattern = (
        re.escape(_SENTINEL_BEGIN) + r".*?" + re.escape(_SENTINEL_END) + r"\n?"
    )
    return re.sub(pattern, block, content, flags=re.DOTALL)


# ---------------------------------------------------------------------------
# Install logic
# ---------------------------------------------------------------------------

def _install_bash_zsh(shell: str, *, dry_run: bool, force: bool) -> int:
    """Install completions for bash or zsh by writing the script into the profile."""
    from grove.cli import build_parser
    from grove.repo_utils import Colors

    profile_path = _get_profile_path(shell)
    assert profile_path is not None  # fish is handled by _install_fish

    parser = build_parser()
    structure = extract_structure(parser)
    script = _GENERATORS[shell](structure)
    block = _build_profile_block(script)

    if profile_path.exists():
        content = profile_path.read_text()
        if _has_grove_block(content):
            new_content = _replace_block(content, block)
            if new_content == content and not force:
                print(f"Grove completions already installed in {profile_path}")
                return 0
            if dry_run:
                if new_content == content:
                    print(f"Would re-write grove completions in {profile_path} (--force)")
                else:
                    print(f"Would update grove completions in {profile_path}")
                return 0
            profile_path.write_text(new_content)
            print(f"Updated grove completions in {Colors.blue(str(profile_path))}")
        else:
            new_content = _inject_block(content, block)
            if dry_run:
                print(f"Would add grove completions to {profile_path}")
                return 0
            profile_path.write_text(new_content)
            print(f"Added grove completions to {Colors.blue(str(profile_path))}")
    else:
        if dry_run:
            print(f"Would create {profile_path} with grove completions")
            return 0
        profile_path.write_text(block)
        print(f"Created {Colors.blue(str(profile_path))} with grove completions")

    print()
    print("To activate now, run:")
    print(f"  source {profile_path}")
    print()
    print("Or restart your shell.")
    return 0


def _install_fish(*, dry_run: bool, force: bool) -> int:
    """Install completions for fish by writing the completions file."""
    from grove.cli import build_parser
    from grove.repo_utils import Colors

    fish_path = _get_fish_completions_path()

    parser = build_parser()
    structure = extract_structure(parser)
    script = _generate_fish(structure)

    existing = fish_path.read_text() if fish_path.exists() else None
    if existing == script and not force:
        print(f"Grove completions already installed at {fish_path}")
        return 0

    if dry_run:
        if existing is None:
            print(f"Would install grove completions to {fish_path}")
        elif existing == script:
            print(f"Would re-write grove completions at {fish_path} (--force)")
        else:
            print(f"Would update grove completions at {fish_path}")
        return 0

    fish_path.parent.mkdir(parents=True, exist_ok=True)
    fish_path.write_text(script)
    action = "Updated" if existing is not None else "Installed"
    print(f"{action} grove completions at {Colors.blue(str(fish_path))}")
    print()
    print("Completions will be loaded automatically in new fish sessions.")
    return 0


# ---------------------------------------------------------------------------
# Check logic
# ---------------------------------------------------------------------------

def _check_installed(shell: str) -> int:
    """Check if completions are installed for the given shell."""
    from grove.repo_utils import Colors

    if shell == "fish":
        fish_path = _get_fish_completions_path()
        if fish_path.exists():
            print(f"  {Colors.green('installed')}  fish  ({fish_path})")
        else:
            print(f"  {Colors.yellow('missing')}    fish  ({fish_path})")
            print(f"\nRun {Colors.blue('grove completion install')} to install.")
        return 0

    profile_path = _get_profile_path(shell)
    assert profile_path is not None  # fish is handled above
    if profile_path.exists() and _has_grove_block(profile_path.read_text()):
        print(f"  {Colors.green('installed')}  {shell}  ({profile_path})")
    else:
        print(f"  {Colors.yellow('missing')}    {shell}  ({profile_path})")
        print(f"\nRun {Colors.blue('grove completion install')} to install.")
    return 0


# ---------------------------------------------------------------------------
# Entry point for install subcommand
# ---------------------------------------------------------------------------

def run_install(args) -> int:
    """Install shell completions or check installation status."""
    from grove.repo_utils import Colors

    shell = args.shell or _detect_shell()
    if shell is None:
        print(Colors.red("Error: could not detect shell from $SHELL."))
        print("Specify explicitly with --shell:")
        print("  grove completion install --shell bash")
        print("  grove completion install --shell zsh")
        print("  grove completion install --shell fish")
        return 1

    if args.check:
        return _check_installed(shell)

    dry_run = args.dry_run
    force = args.force

    if shell in ("bash", "zsh"):
        return _install_bash_zsh(shell, dry_run=dry_run, force=force)
    if shell == "fish":
        return _install_fish(dry_run=dry_run, force=force)

    print(Colors.red(f"Error: unsupported shell '{shell}'."))
    return 1

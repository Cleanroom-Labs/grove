"""
grove.completion
Generate shell completion scripts by introspecting the argparse parser.
"""

from __future__ import annotations

import argparse


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
    lines.append('_grove "$@"')
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
    script = _GENERATORS[args.shell](structure)
    print(script)
    return 0

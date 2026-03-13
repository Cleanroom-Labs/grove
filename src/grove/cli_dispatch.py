"""
grove.cli_dispatch
CLI command dispatch helpers.
"""


def dispatch_command(args, parser):
    """Dispatch parsed CLI args to the appropriate command handler."""
    if args.command == "init":
        from grove.init import run

        return run(args)

    if args.command == "check":
        from grove.check import run

        return run(args)

    if args.command == "push":
        from grove.push import run

        return run(args)

    if args.command == "sync":
        from grove.sync import run

        return run(args)

    if args.command == "checkout":
        from grove.checkout import run

        return run(args)

    if args.command == "cascade":
        from grove.cascade import run

        return run(args)

    if args.command == "visualize":
        from grove.visualizer.__main__ import run

        return run(args)

    if args.command == "shell":
        if not args.shell_command:
            parser.grove_subparsers["shell"].print_help()
            return 2
        from grove.shell import run

        return run(args)

    if args.command == "worktree":
        if not args.worktree_command:
            parser.grove_subparsers["worktree"].print_help()
            return 2
        if args.worktree_command == "merge":
            from grove.worktree_merge import run

            return run(args)
        from grove.worktree import run

        return run(args)

    if args.command == "claude":
        if not args.claude_command:
            parser.grove_subparsers["claude"].print_help()
            return 2
        if args.claude_command == "install":
            from grove.claude import run_install

            return run_install(args)

    if args.command == "config":
        if not args.config_command:
            parser.grove_subparsers["config"].print_help()
            return 2
        if args.config_command == "import-wt":
            from grove.config_import import run

            return run(args)

    if args.command == "completion":
        if not args.completion_command:
            parser.grove_subparsers["completion"].print_help()
            return 2
        if args.completion_command == "install":
            from grove.completion import run_install

            return run_install(args)
        from grove.completion import run

        return run(args)

    parser.print_help()
    return 2

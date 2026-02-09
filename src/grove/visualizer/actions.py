"""
Git operation handlers for the visualizer.

Provides functions for fetch, push, and checkout operations
with proper error handling and UI feedback.
"""

import threading
import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from grove.repo_utils import RepoInfo


class BranchPickerDialog(simpledialog.Dialog):
    """Dialog for selecting a branch to checkout."""

    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        local_branches: list[str],
        remote_branches: list[str],
        current_branch: str | None,
    ):
        self.local_branches = local_branches
        self.remote_branches = remote_branches
        self.current_branch = current_branch
        self.selected_branch: str | None = None
        super().__init__(parent, title)

    def body(self, master: tk.Frame) -> tk.Widget | None:
        """Create dialog body."""
        # Local branches section
        tk.Label(master, text="Local branches:", font=('TkDefaultFont', 10, 'bold')).pack(
            anchor='w', padx=10, pady=(10, 5)
        )

        self.local_listbox = tk.Listbox(master, height=6, width=40, exportselection=False)
        self.local_listbox.pack(padx=10, fill='x')

        for branch in self.local_branches:
            display = f"* {branch}" if branch == self.current_branch else f"  {branch}"
            self.local_listbox.insert(tk.END, display)

        self.local_listbox.bind('<<ListboxSelect>>', self._on_local_select)

        # Remote branches section
        tk.Label(master, text="Remote branches:", font=('TkDefaultFont', 10, 'bold')).pack(
            anchor='w', padx=10, pady=(10, 5)
        )

        self.remote_listbox = tk.Listbox(master, height=6, width=40, exportselection=False)
        self.remote_listbox.pack(padx=10, fill='x')

        for branch in self.remote_branches:
            # Only show remote branches not in local
            if branch not in self.local_branches:
                self.remote_listbox.insert(tk.END, f"  {branch}")

        self.remote_listbox.bind('<<ListboxSelect>>', self._on_remote_select)

        return self.local_listbox

    def _on_local_select(self, event: tk.Event) -> None:
        """Handle local branch selection."""
        self.remote_listbox.selection_clear(0, tk.END)
        selection = self.local_listbox.curselection()
        if selection:
            idx = selection[0]
            self.selected_branch = self.local_branches[idx]

    def _on_remote_select(self, event: tk.Event) -> None:
        """Handle remote branch selection."""
        self.local_listbox.selection_clear(0, tk.END)
        selection = self.remote_listbox.curselection()
        if selection:
            # Get the branch name (strip leading spaces)
            text = self.remote_listbox.get(selection[0])
            self.selected_branch = text.strip()

    def apply(self) -> None:
        """Called when OK is pressed."""
        # selected_branch is already set by selection handlers
        pass

    def buttonbox(self) -> None:
        """Add standard button box."""
        box = tk.Frame(self)

        tk.Button(
            box, text="Checkout", width=10, command=self.ok, default=tk.ACTIVE
        ).pack(side=tk.LEFT, padx=5, pady=10)

        tk.Button(
            box, text="Cancel", width=10, command=self.cancel
        ).pack(side=tk.LEFT, padx=5, pady=10)

        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

        box.pack()


class ActionHandler:
    """Handles git operations for the visualizer."""

    def __init__(
        self,
        root: tk.Tk,
        on_refresh: Callable[[], None],
        on_status: Callable[[str], None],
    ):
        """
        Initialize action handler.

        Args:
            root: Root tkinter window
            on_refresh: Callback to refresh the display
            on_status: Callback to update status bar
        """
        self.root = root
        self.on_refresh = on_refresh
        self.on_status = on_status

    def fetch(self, repo: 'RepoInfo') -> None:
        """Fetch from remote for a single repository."""
        self.on_status(f"Fetching {repo.name}...")

        def do_fetch() -> None:
            success = repo.fetch()
            self.root.after(0, lambda: self._on_fetch_complete(repo, success))

        thread = threading.Thread(target=do_fetch, daemon=True)
        thread.start()

    def _on_fetch_complete(self, repo: 'RepoInfo', success: bool) -> None:
        """Handle fetch completion on main thread."""
        if success:
            self.on_status(f"Fetched {repo.name}")
            # Re-validate to update ahead/behind counts
            repo.validate(check_sync=True)
            self.on_refresh()
        else:
            self.on_status(f"Fetch failed for {repo.name}")
            messagebox.showerror(
                "Fetch Failed",
                f"Failed to fetch from remote for {repo.name}",
            )

    def fetch_all(self, repos: list['RepoInfo']) -> None:
        """Fetch from remote for all repositories."""
        self.on_status("Fetching all repositories...")

        def do_fetch_all() -> None:
            failed = []
            for repo in repos:
                if not repo.fetch():
                    failed.append(repo.name)
            self.root.after(0, lambda: self._on_fetch_all_complete(repos, failed))

        thread = threading.Thread(target=do_fetch_all, daemon=True)
        thread.start()

    def _on_fetch_all_complete(
        self, repos: list['RepoInfo'], failed: list[str]
    ) -> None:
        """Handle fetch all completion on main thread."""
        # Re-validate all repos
        for repo in repos:
            repo.validate(check_sync=True)

        self.on_refresh()

        if failed:
            self.on_status(f"Fetch completed with {len(failed)} failures")
            messagebox.showwarning(
                "Fetch Completed with Errors",
                f"Failed to fetch: {', '.join(failed)}",
            )
        else:
            self.on_status("Fetch completed for all repositories")

    def push(self, repo: 'RepoInfo') -> None:
        """Push a single repository."""
        # Validate first
        if not repo.validate():
            messagebox.showerror(
                "Cannot Push",
                f"Repository {repo.name} is not in a valid state:\n{repo.error_message}",
            )
            return

        if repo.ahead_count == "0":
            messagebox.showinfo("Nothing to Push", f"{repo.name} is already up to date")
            return

        self.on_status(f"Pushing {repo.name}...")

        def do_push() -> None:
            success = repo.push()
            self.root.after(0, lambda: self._on_push_complete(repo, success))

        thread = threading.Thread(target=do_push, daemon=True)
        thread.start()

    def _on_push_complete(self, repo: 'RepoInfo', success: bool) -> None:
        """Handle push completion on main thread."""
        if success:
            self.on_status(f"Pushed {repo.name}")
            repo.validate()
            self.on_refresh()
        else:
            self.on_status(f"Push failed for {repo.name}")
            messagebox.showerror(
                "Push Failed",
                f"Failed to push {repo.name}. Check the terminal for details.",
            )

    def push_all(self, repos: list['RepoInfo']) -> None:
        """Push all repositories in dependency order."""
        # Import here to avoid circular imports
        from grove.repo_utils import topological_sort_repos

        # Validate all repos first
        invalid = []
        for repo in repos:
            if not repo.validate():
                invalid.append(repo)

        if invalid:
            names = [r.name for r in invalid]
            messagebox.showerror(
                "Cannot Push",
                f"Some repositories are not in a valid state: {', '.join(names)}",
            )
            return

        # Filter to repos that need pushing
        to_push = [r for r in repos if r.ahead_count not in ("0", None)]

        if not to_push:
            messagebox.showinfo("Nothing to Push", "All repositories are up to date")
            return

        # Sort in dependency order
        sorted_repos = topological_sort_repos(to_push)

        self.on_status(f"Pushing {len(sorted_repos)} repositories...")

        def do_push_all() -> None:
            failed = []
            for repo in sorted_repos:
                if not repo.push():
                    failed.append(repo.name)
            self.root.after(0, lambda: self._on_push_all_complete(repos, failed))

        thread = threading.Thread(target=do_push_all, daemon=True)
        thread.start()

    def _on_push_all_complete(
        self, repos: list['RepoInfo'], failed: list[str]
    ) -> None:
        """Handle push all completion on main thread."""
        # Re-validate all repos
        for repo in repos:
            repo.validate()

        self.on_refresh()

        if failed:
            self.on_status(f"Push completed with {len(failed)} failures")
            messagebox.showerror(
                "Push Completed with Errors",
                f"Failed to push: {', '.join(failed)}",
            )
        else:
            self.on_status("Push completed for all repositories")

    def checkout(self, repo: 'RepoInfo') -> None:
        """Show branch picker and checkout selected branch."""
        local_branches = repo.get_local_branches()
        remote_branches = repo.get_remote_branches()

        dialog = BranchPickerDialog(
            self.root,
            f"Checkout Branch - {repo.name}",
            local_branches,
            remote_branches,
            repo.branch,
        )

        if dialog.selected_branch:
            self.on_status(f"Checking out {dialog.selected_branch}...")
            success, error = repo.checkout(dialog.selected_branch)

            if success:
                self.on_status(f"Checked out {dialog.selected_branch}")
                repo.validate(check_sync=True)
                self.on_refresh()
            else:
                self.on_status(f"Checkout failed")
                messagebox.showerror(
                    "Checkout Failed",
                    f"Failed to checkout {dialog.selected_branch}:\n{error}",
                )

    def show_context_menu(
        self, repo: 'RepoInfo', x: int, y: int, repos: list['RepoInfo']
    ) -> None:
        """Show context menu for a repository node."""
        menu = tk.Menu(self.root, tearoff=0)

        menu.add_command(label=f"Fetch {repo.name}", command=lambda: self.fetch(repo))
        menu.add_command(label=f"Push {repo.name}", command=lambda: self.push(repo))
        menu.add_command(label=f"Checkout Branch...", command=lambda: self.checkout(repo))
        menu.add_separator()
        menu.add_command(label="Fetch All", command=lambda: self.fetch_all(repos))
        menu.add_command(label="Push All", command=lambda: self.push_all(repos))

        menu.tk_popup(x, y)

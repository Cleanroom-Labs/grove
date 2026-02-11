"""
Main application window for the git submodule visualizer.

Provides the main window with menu bar, graph canvas, and status bar.
"""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING

from .actions import ActionHandler
from .graph_canvas import GraphCanvas

if TYPE_CHECKING:
    from grove.repo_utils import RepoInfo


class SubmoduleVisualizerApp:
    """Main application for visualizing git submodules."""

    WINDOW_TITLE = "Git Submodule Visualizer"
    DEFAULT_WIDTH = 1000
    DEFAULT_HEIGHT = 700

    def __init__(self, repo_path: Path | None = None):
        """
        Initialize the application.

        Args:
            repo_path: Path to the git repository. Defaults to current directory.
        """
        self.repo_path = repo_path or Path.cwd()
        self.repos: list['RepoInfo'] = []

        # Create main window
        self.root = tk.Tk()
        self.root.title(self.WINDOW_TITLE)
        self.root.geometry(f"{self.DEFAULT_WIDTH}x{self.DEFAULT_HEIGHT}")

        # Configure grid weights for resizing
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        # Create menu bar
        self._create_menu_bar()

        # Create main frame
        self.main_frame = tk.Frame(self.root)
        self.main_frame.grid(row=0, column=0, sticky='nsew')
        self.main_frame.grid_rowconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)

        # Create action handler
        self.action_handler = ActionHandler(
            root=self.root,
            on_refresh=self.refresh,
            on_status=self._update_status,
        )

        # Create graph canvas with scrollbars
        self.canvas_frame = tk.Frame(self.main_frame)
        self.canvas_frame.grid(row=0, column=0, sticky='nsew')
        self.canvas_frame.grid_rowconfigure(0, weight=1)
        self.canvas_frame.grid_columnconfigure(0, weight=1)

        # Scrollbars
        self.h_scrollbar = tk.Scrollbar(self.canvas_frame, orient=tk.HORIZONTAL)
        self.h_scrollbar.grid(row=1, column=0, sticky='ew')

        self.v_scrollbar = tk.Scrollbar(self.canvas_frame, orient=tk.VERTICAL)
        self.v_scrollbar.grid(row=0, column=1, sticky='ns')

        # Canvas
        self.graph_canvas = GraphCanvas(
            self.canvas_frame,
            on_select=self._on_node_select,
            on_right_click=self._on_node_right_click,
            xscrollcommand=self.h_scrollbar.set,
            yscrollcommand=self.v_scrollbar.set,
        )
        self.graph_canvas.grid(row=0, column=0, sticky='nsew')

        self.h_scrollbar.config(command=self.graph_canvas.xview)
        self.v_scrollbar.config(command=self.graph_canvas.yview)

        # Create status bar
        self._create_status_bar()

        # Create details panel (optional, shows selected repo info)
        self._create_details_panel()

        # Load repos
        self._load_repos()

    def _create_menu_bar(self) -> None:
        """Create the menu bar."""
        self.menu_bar = tk.Menu(self.root)
        self.root.config(menu=self.menu_bar)

        # File menu
        file_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Open Repository...", command=self._open_repository)
        file_menu.add_command(
            label="Refresh", command=self.refresh, accelerator="Cmd+R"
        )
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.root.quit, accelerator="Cmd+Q")

        # Actions menu
        actions_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Actions", menu=actions_menu)
        actions_menu.add_command(
            label="Fetch All", command=lambda: self.action_handler.fetch_all(self.repos)
        )
        actions_menu.add_command(
            label="Push All", command=lambda: self.action_handler.push_all(self.repos)
        )

        # Bind keyboard shortcuts
        self.root.bind('<Command-r>', lambda e: self.refresh())
        self.root.bind('<Command-q>', lambda e: self.root.quit())
        # Also bind Control for Windows/Linux
        self.root.bind('<Control-r>', lambda e: self.refresh())
        self.root.bind('<Control-q>', lambda e: self.root.quit())

    def _create_status_bar(self) -> None:
        """Create the status bar at the bottom."""
        self.status_frame = tk.Frame(self.main_frame, relief=tk.SUNKEN, bd=1)
        self.status_frame.grid(row=2, column=0, sticky='ew')

        self.status_label = tk.Label(
            self.status_frame,
            text="Ready",
            anchor='w',
            padx=10,
            pady=4,
        )
        self.status_label.pack(fill='x')

    def _create_details_panel(self) -> None:
        """Create the details panel for showing selected repo info."""
        self.details_frame = tk.Frame(
            self.main_frame, relief=tk.GROOVE, bd=1, padx=10, pady=5
        )
        self.details_frame.grid(row=1, column=0, sticky='ew')

        # Repo name
        self.details_name_label = tk.Label(
            self.details_frame,
            text="Select a repository to view details",
            font=('TkDefaultFont', 11, 'bold'),
            anchor='w',
        )
        self.details_name_label.pack(fill='x')

        # Details text
        self.details_text = tk.Label(
            self.details_frame,
            text="",
            anchor='w',
            justify='left',
        )
        self.details_text.pack(fill='x')

    def _update_status(self, message: str) -> None:
        """Update the status bar message."""
        self.status_label.config(text=message)

    def _on_node_select(self, repo: 'RepoInfo') -> None:
        """Handle node selection."""
        # Update details panel
        name = repo.name
        if repo.path == repo.repo_root:
            name = f"{name} (root)"

        self.details_name_label.config(text=name)

        details = []
        details.append(f"Path: {repo.rel_path}")
        details.append(f"Branch: {repo.branch or 'detached HEAD'}")
        details.append(f"Commit: {repo.get_commit_sha()}")

        ahead = repo.ahead_count or '0'
        behind = repo.behind_count or '0'
        details.append(f"Ahead: {ahead}, Behind: {behind}")
        details.append(f"Status: {repo.status.value if repo.status else 'unknown'}")

        if repo.sync_group:
            details.append(f"Sync group: {repo.sync_group}")

        if repo.error_message:
            details.append(f"Error: {repo.error_message}")

        self.details_text.config(text='\n'.join(details))

        self._update_status(f"Selected: {repo.rel_path}")

    def _on_node_right_click(self, repo: 'RepoInfo', x: int, y: int) -> None:
        """Handle right-click on a node."""
        self.action_handler.show_context_menu(repo, x, y, self.repos)

    def _open_repository(self) -> None:
        """Open a different repository."""
        path = filedialog.askdirectory(
            title="Select Git Repository",
            initialdir=str(self.repo_path),
        )
        if path:
            self.repo_path = Path(path)
            self._load_repos()

    # Palette of visually distinct colors for sync-group borders
    SYNC_GROUP_PALETTE = [
        '#2196F3',  # Blue
        '#9C27B0',  # Purple
        '#009688',  # Teal
        '#E91E63',  # Pink
        '#3F51B5',  # Indigo
        '#00BCD4',  # Cyan
        '#795548',  # Brown
        '#607D8B',  # Blue Gray
    ]

    def _load_repos(self) -> None:
        """Load repositories from the current path."""
        from grove.repo_utils import discover_repos_from_gitmodules

        self._update_status(f"Loading {self.repo_path}...")

        try:
            self.repos = discover_repos_from_gitmodules(self.repo_path)

            # Validate all repos
            for repo in self.repos:
                repo.validate(check_sync=True)

            # Populate sync-group membership for visualization
            self._populate_sync_groups()

            self.root.title(f"{self.WINDOW_TITLE} - {self.repo_path.name}")
            self.graph_canvas.set_repos(self.repos)
            self._update_status(f"Loaded {len(self.repos)} repositories")

        except Exception as e:
            self._update_status(f"Error loading repositories: {e}")
            self.graph_canvas.clear()

    def _populate_sync_groups(self) -> None:
        """Tag repos with their sync-group name and color."""
        try:
            from grove.config import load_config
            from grove.sync import discover_sync_submodules

            config = load_config(self.repo_path)
        except (FileNotFoundError, ValueError):
            return

        # Build path -> (group_name, color) mapping
        path_to_group: dict[Path, tuple[str, str]] = {}
        for i, group in enumerate(config.sync_groups.values()):
            color = self.SYNC_GROUP_PALETTE[i % len(self.SYNC_GROUP_PALETTE)]
            for sub in discover_sync_submodules(self.repo_path, group.url_match):
                path_to_group[sub.path] = (group.name, color)

        for repo in self.repos:
            if repo.path in path_to_group:
                repo.sync_group, repo.sync_group_color = path_to_group[repo.path]

    def refresh(self) -> None:
        """Refresh the display."""
        self._load_repos()

    def run(self) -> None:
        """Run the application main loop."""
        self.root.mainloop()

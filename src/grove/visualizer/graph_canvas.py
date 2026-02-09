"""
Graph canvas widget for visualizing repository node-link diagram.

Displays repos as nodes with edges connecting parents to children.
Handles click events for node selection.
"""

import tkinter as tk
from typing import Callable, TYPE_CHECKING

from .layout import NodeLayout, TreeLayout
from .repo_node import RepoNode

if TYPE_CHECKING:
    from grove.repo_utils import RepoInfo


class GraphCanvas(tk.Canvas):
    """Canvas widget for displaying repository graph."""

    EDGE_COLOR = '#888888'
    EDGE_WIDTH = 2
    BACKGROUND_COLOR = '#F5F5F5'

    def __init__(
        self,
        parent: tk.Widget,
        on_select: Callable[['RepoInfo'], None] | None = None,
        on_right_click: Callable[['RepoInfo', int, int], None] | None = None,
        **kwargs,
    ):
        kwargs.setdefault('bg', self.BACKGROUND_COLOR)
        kwargs.setdefault('highlightthickness', 0)
        super().__init__(parent, **kwargs)

        self.on_select = on_select
        self.on_right_click = on_right_click

        self.layout = TreeLayout()
        self.nodes: list[RepoNode] = []
        self.edges: list[int] = []  # Canvas item IDs for edges
        self.root_layout: NodeLayout | None = None
        self.selected_repo: 'RepoInfo' | None = None

        # Pan/scroll state
        self._drag_start_x = 0
        self._drag_start_y = 0

        # Bind events
        self.bind('<Button-1>', self._on_click)
        self.bind('<Button-2>', self._on_right_click_event)  # Middle click on some systems
        self.bind('<Button-3>', self._on_right_click_event)  # Right click
        self.bind('<B1-Motion>', self._on_drag)
        self.bind('<ButtonPress-1>', self._on_drag_start)

        # Scroll bindings
        self.bind('<MouseWheel>', self._on_mousewheel)  # Windows/macOS
        self.bind('<Button-4>', self._on_scroll_up)  # Linux
        self.bind('<Button-5>', self._on_scroll_down)  # Linux

    def set_repos(self, repos: list['RepoInfo']) -> None:
        """Set the repositories to display and layout the graph."""
        self.clear()

        if not repos:
            return

        # Calculate layout
        self.root_layout, total_width, total_height = self.layout.calculate(repos)

        if self.root_layout is None:
            return

        # Configure scroll region
        self.configure(scrollregion=(0, 0, total_width, total_height))

        # Draw edges first (so they're behind nodes)
        self._draw_edges(self.root_layout)

        # Draw nodes
        self._draw_nodes(self.root_layout)

        # Restore selection if repo still exists
        if self.selected_repo:
            for node in self.nodes:
                if node.repo.path == self.selected_repo.path:
                    node.set_selected(True)
                    break

    def _draw_edges(self, layout: NodeLayout) -> None:
        """Recursively draw edges from parent to children."""
        for child_layout in layout.children:
            # Draw edge from parent bottom-center to child top-center
            edge_id = self.create_line(
                layout.center_x,
                layout.bottom,
                child_layout.center_x,
                child_layout.top,
                fill=self.EDGE_COLOR,
                width=self.EDGE_WIDTH,
                smooth=True,
            )
            self.edges.append(edge_id)

            # Recurse for children
            self._draw_edges(child_layout)

    def _draw_nodes(self, layout: NodeLayout) -> None:
        """Recursively draw nodes."""
        node = RepoNode(
            canvas=self,
            x=layout.left,
            y=layout.top,
            width=layout.width,
            height=layout.height,
            repo=layout.repo,
        )
        node.draw()
        self.nodes.append(node)

        for child_layout in layout.children:
            self._draw_nodes(child_layout)

    def clear(self) -> None:
        """Clear all nodes and edges from the canvas."""
        self.delete('all')
        self.nodes = []
        self.edges = []
        self.root_layout = None

    def _on_click(self, event: tk.Event) -> None:
        """Handle click events to select nodes."""
        # Convert to canvas coordinates
        cx = self.canvasx(event.x)
        cy = self.canvasy(event.y)

        # Find clicked node
        clicked_node = None
        for node in self.nodes:
            if node.contains_point(cx, cy):
                clicked_node = node
                break

        # Update selection
        for node in self.nodes:
            node.set_selected(node is clicked_node)

        if clicked_node:
            self.selected_repo = clicked_node.repo
            if self.on_select:
                self.on_select(clicked_node.repo)
        else:
            self.selected_repo = None

    def _on_right_click_event(self, event: tk.Event) -> None:
        """Handle right-click events for context menu."""
        cx = self.canvasx(event.x)
        cy = self.canvasy(event.y)

        # Find clicked node
        for node in self.nodes:
            if node.contains_point(cx, cy):
                # Select the node
                for n in self.nodes:
                    n.set_selected(n is node)
                self.selected_repo = node.repo

                if self.on_right_click:
                    self.on_right_click(node.repo, event.x_root, event.y_root)
                break

    def _on_drag_start(self, event: tk.Event) -> None:
        """Remember drag start position."""
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_drag(self, event: tk.Event) -> None:
        """Handle drag to pan the canvas."""
        dx = event.x - self._drag_start_x
        dy = event.y - self._drag_start_y

        self.xview_scroll(-dx, 'units')
        self.yview_scroll(-dy, 'units')

        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_mousewheel(self, event: tk.Event) -> None:
        """Handle mouse wheel for vertical scrolling."""
        # On macOS, delta is in units of 1
        # On Windows, delta is in units of 120
        delta = -1 if event.delta > 0 else 1
        self.yview_scroll(delta, 'units')

    def _on_scroll_up(self, event: tk.Event) -> None:
        """Handle scroll up (Linux)."""
        self.yview_scroll(-1, 'units')

    def _on_scroll_down(self, event: tk.Event) -> None:
        """Handle scroll down (Linux)."""
        self.yview_scroll(1, 'units')

    def get_selected_repo(self) -> 'RepoInfo | None':
        """Get the currently selected repository."""
        return self.selected_repo

    def select_repo(self, repo: 'RepoInfo') -> None:
        """Programmatically select a repository."""
        for node in self.nodes:
            if node.repo.path == repo.path:
                node.set_selected(True)
                self.selected_repo = repo
                if self.on_select:
                    self.on_select(repo)
            else:
                node.set_selected(False)

    def refresh_node(self, repo: 'RepoInfo') -> None:
        """Refresh the display of a specific node after its state changes."""
        for node in self.nodes:
            if node.repo.path == repo.path:
                node.redraw()
                break

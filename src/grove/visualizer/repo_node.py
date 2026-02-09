"""
Repository node rendering for the graph canvas.

Handles drawing nodes as rounded rectangles with repo information
and status color coding.
"""

import tkinter as tk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grove.repo_utils import RepoInfo

# Status colors
STATUS_COLORS = {
    'UP_TO_DATE': '#4CAF50',  # Green
    'OK': '#4CAF50',  # Green
    'PENDING': '#FFC107',  # Yellow/Amber
    'BEHIND': '#FF9800',  # Orange
    'DIVERGED': '#F44336',  # Red
    'UNCOMMITTED': '#F44336',  # Red
    'DETACHED': '#9E9E9E',  # Gray
    'NO_REMOTE': '#9E9E9E',  # Gray
}

# Status display text
STATUS_TEXT = {
    'UP_TO_DATE': 'Up to date',
    'OK': 'OK',
    'PENDING': 'Pending',
    'BEHIND': 'Behind',
    'DIVERGED': 'Diverged',
    'UNCOMMITTED': 'Uncommitted',
    'DETACHED': 'Detached',
    'NO_REMOTE': 'No remote',
}


class RepoNode:
    """Handles rendering of a repository node on the canvas."""

    CORNER_RADIUS = 8
    PADDING = 10
    LINE_HEIGHT = 18
    HEADER_HEIGHT = 24

    def __init__(
        self,
        canvas: tk.Canvas,
        x: float,
        y: float,
        width: float,
        height: float,
        repo: 'RepoInfo',
    ):
        self.canvas = canvas
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.repo = repo
        self.items: list[int] = []  # Canvas item IDs
        self.selected = False

    def draw(self) -> list[int]:
        """Draw the node and return list of canvas item IDs."""
        self.items = []

        # Get status info
        status_name = self.repo.status.name if self.repo.status else 'OK'
        fill_color = STATUS_COLORS.get(status_name, '#9E9E9E')
        status_text = STATUS_TEXT.get(status_name, status_name)

        # Calculate positions
        left = self.x
        top = self.y
        right = self.x + self.width
        bottom = self.y + self.height

        # Draw rounded rectangle background
        bg_color = '#FFFFFF'
        if self.repo.sync_group_color:
            border_color = self.repo.sync_group_color
            border_width = 3 if self.selected else 2
        else:
            border_color = fill_color if self.selected else '#CCCCCC'
            border_width = 3 if self.selected else 1

        bg_id = self._draw_rounded_rect(
            left, top, right, bottom,
            self.CORNER_RADIUS,
            fill=bg_color,
            outline=border_color,
            width=border_width,
        )
        self.items.append(bg_id)

        # Draw header bar
        header_bottom = top + self.HEADER_HEIGHT
        header_id = self._draw_rounded_rect(
            left + 1, top + 1, right - 1, header_bottom,
            self.CORNER_RADIUS,
            fill=fill_color,
            outline='',
        )
        self.items.append(header_id)

        # Clip the bottom corners of header
        clip_id = self.canvas.create_rectangle(
            left + 1, header_bottom - self.CORNER_RADIUS,
            right - 1, header_bottom,
            fill=fill_color, outline='',
        )
        self.items.append(clip_id)

        # Draw repo name (header)
        name = self.repo.name
        if self.repo.path == self.repo.repo_root:
            name = f"{name} (root)"
        name_id = self.canvas.create_text(
            left + self.PADDING,
            top + self.HEADER_HEIGHT / 2,
            text=name,
            anchor='w',
            font=('TkDefaultFont', 10, 'bold'),
            fill='white',
        )
        self.items.append(name_id)

        # Draw info lines
        y_pos = header_bottom + self.PADDING

        # Branch
        branch_text = self.repo.branch or 'detached HEAD'
        branch_id = self.canvas.create_text(
            left + self.PADDING,
            y_pos,
            text=f"Branch: {branch_text}",
            anchor='w',
            font=('TkDefaultFont', 9),
            fill='#333333',
        )
        self.items.append(branch_id)
        y_pos += self.LINE_HEIGHT

        # Commit SHA
        commit_sha = self.repo.get_commit_sha(short=True)
        sha_id = self.canvas.create_text(
            left + self.PADDING,
            y_pos,
            text=f"Commit: {commit_sha}",
            anchor='w',
            font=('TkDefaultFont', 9),
            fill='#666666',
        )
        self.items.append(sha_id)
        y_pos += self.LINE_HEIGHT

        # Ahead/behind
        ahead = self.repo.ahead_count or '0'
        behind = self.repo.behind_count or '0'
        ahead_behind_id = self.canvas.create_text(
            left + self.PADDING,
            y_pos,
            text=f"\u2191{ahead} \u2193{behind}",
            anchor='w',
            font=('TkDefaultFont', 9),
            fill='#666666',
        )
        self.items.append(ahead_behind_id)
        y_pos += self.LINE_HEIGHT

        # Status indicator
        indicator_size = 8
        indicator_x = left + self.PADDING + indicator_size / 2
        indicator_y = y_pos + indicator_size / 2

        indicator_id = self.canvas.create_oval(
            indicator_x - indicator_size / 2,
            indicator_y - indicator_size / 2,
            indicator_x + indicator_size / 2,
            indicator_y + indicator_size / 2,
            fill=fill_color,
            outline='',
        )
        self.items.append(indicator_id)

        status_label_id = self.canvas.create_text(
            indicator_x + indicator_size / 2 + 6,
            indicator_y,
            text=status_text,
            anchor='w',
            font=('TkDefaultFont', 9),
            fill='#333333',
        )
        self.items.append(status_label_id)

        return self.items

    def _draw_rounded_rect(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        radius: float,
        **kwargs,
    ) -> int:
        """Draw a rounded rectangle using polygon."""
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
            x1 + radius, y1,
        ]
        return self.canvas.create_polygon(points, smooth=True, **kwargs)

    def contains_point(self, px: float, py: float) -> bool:
        """Check if point is inside this node."""
        return (
            self.x <= px <= self.x + self.width and
            self.y <= py <= self.y + self.height
        )

    def set_selected(self, selected: bool) -> None:
        """Update selection state and redraw."""
        if self.selected != selected:
            self.selected = selected
            self.redraw()

    def redraw(self) -> None:
        """Delete and redraw the node."""
        for item_id in self.items:
            self.canvas.delete(item_id)
        self.draw()

    def delete(self) -> None:
        """Remove all canvas items for this node."""
        for item_id in self.items:
            self.canvas.delete(item_id)
        self.items = []

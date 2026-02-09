"""
Tree layout algorithm for positioning repository nodes.

Implements a simple top-down tree layout where:
- Root repo at top center
- Children positioned below parent, spread horizontally
- Edges connect parent to children
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grove.repo_utils import RepoInfo


@dataclass
class NodeLayout:
    """Layout information for a single node."""
    repo: 'RepoInfo'
    x: float  # Center x position
    y: float  # Top y position
    width: float
    height: float
    children: list['NodeLayout']

    @property
    def center_x(self) -> float:
        return self.x

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2

    @property
    def top(self) -> float:
        return self.y

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def left(self) -> float:
        return self.x - self.width / 2

    @property
    def right(self) -> float:
        return self.x + self.width / 2


class TreeLayout:
    """Calculates tree layout for repository nodes."""

    def __init__(
        self,
        node_width: float = 200,
        node_height: float = 100,
        horizontal_gap: float = 40,
        vertical_gap: float = 60,
        padding: float = 40,
    ):
        self.node_width = node_width
        self.node_height = node_height
        self.horizontal_gap = horizontal_gap
        self.vertical_gap = vertical_gap
        self.padding = padding

    def calculate(
        self,
        repos: list['RepoInfo'],
    ) -> tuple[NodeLayout | None, float, float]:
        """
        Calculate layout for all repos.

        Args:
            repos: List of RepoInfo objects with parent relationships set

        Returns:
            Tuple of (root_layout, total_width, total_height)
        """
        if not repos:
            return None, 0, 0

        # Find root repo (the one without a parent)
        root_repo = None
        for repo in repos:
            if repo.parent is None:
                root_repo = repo
                break

        if root_repo is None:
            return None, 0, 0

        # Build lookup for children using path as key (RepoInfo is not hashable)
        children_map: dict[Path, list['RepoInfo']] = {repo.path: [] for repo in repos}
        for repo in repos:
            if repo.parent is not None:
                children_map[repo.parent.path].append(repo)

        # Sort children by name for consistent ordering
        for children in children_map.values():
            children.sort(key=lambda r: r.name)

        # Calculate layout recursively
        root_layout = self._layout_subtree(root_repo, children_map, 0)

        # Calculate bounding box
        min_x, max_x, max_y = self._get_bounds(root_layout)

        # Shift everything so it starts at padding
        self._shift_subtree(root_layout, self.padding - min_x, self.padding)

        total_width = (max_x - min_x) + 2 * self.padding
        total_height = max_y + 2 * self.padding

        return root_layout, total_width, total_height

    def _layout_subtree(
        self,
        repo: 'RepoInfo',
        children_map: dict[Path, list['RepoInfo']],
        depth: int,
    ) -> NodeLayout:
        """Recursively layout a subtree, returns the root NodeLayout."""
        children = children_map.get(repo.path, [])

        # Layout children first
        child_layouts = []
        for child in children:
            child_layout = self._layout_subtree(child, children_map, depth + 1)
            child_layouts.append(child_layout)

        # Calculate y position based on depth
        y = depth * (self.node_height + self.vertical_gap)

        if not child_layouts:
            # Leaf node - position at x=0, will be adjusted later
            return NodeLayout(
                repo=repo,
                x=0,
                y=y,
                width=self.node_width,
                height=self.node_height,
                children=[],
            )

        # Calculate total width of children
        total_children_width = sum(self._subtree_width(c) for c in child_layouts)
        total_children_width += self.horizontal_gap * (len(child_layouts) - 1)

        # Position children
        current_x = -total_children_width / 2
        for child_layout in child_layouts:
            subtree_w = self._subtree_width(child_layout)
            # Center the child within its allocated space
            child_center_x = current_x + subtree_w / 2
            self._shift_subtree(child_layout, child_center_x - child_layout.x, 0)
            current_x += subtree_w + self.horizontal_gap

        # Parent is centered above children
        parent_x = 0  # Already centered

        return NodeLayout(
            repo=repo,
            x=parent_x,
            y=y,
            width=self.node_width,
            height=self.node_height,
            children=child_layouts,
        )

    def _subtree_width(self, layout: NodeLayout) -> float:
        """Calculate the total width of a subtree."""
        if not layout.children:
            return self.node_width

        total = sum(self._subtree_width(c) for c in layout.children)
        total += self.horizontal_gap * (len(layout.children) - 1)
        return max(self.node_width, total)

    def _get_bounds(self, layout: NodeLayout) -> tuple[float, float, float]:
        """Get the bounding box (min_x, max_x, max_y) of the tree."""
        min_x = layout.left
        max_x = layout.right
        max_y = layout.bottom

        for child in layout.children:
            c_min_x, c_max_x, c_max_y = self._get_bounds(child)
            min_x = min(min_x, c_min_x)
            max_x = max(max_x, c_max_x)
            max_y = max(max_y, c_max_y)

        return min_x, max_x, max_y

    def _shift_subtree(self, layout: NodeLayout, dx: float, dy: float) -> None:
        """Shift a subtree by (dx, dy)."""
        layout.x += dx
        layout.y += dy
        for child in layout.children:
            self._shift_subtree(child, dx, dy)


def flatten_layouts(root: NodeLayout | None) -> list[NodeLayout]:
    """Flatten tree of NodeLayouts into a list."""
    if root is None:
        return []

    result = [root]
    for child in root.children:
        result.extend(flatten_layouts(child))
    return result

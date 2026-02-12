/**
 * Tree layout algorithm for positioning repository nodes.
 *
 * Port of the Python TreeLayout from layout.py. Computes a top-down
 * hierarchical layout where the root is at the top and children are
 * spread horizontally below their parent.
 */

const Layout = (() => {
    const NODE_WIDTH = 220;
    const NODE_HEIGHT = 130;
    const H_GAP = 40;
    const V_GAP = 60;
    const PADDING = 40;

    /**
     * Calculate layout positions for a flat list of repo objects.
     *
     * @param {Array} repos - Flat list of repo objects with parent_path fields
     * @param {Set} collapsedPaths - Set of rel_paths that are collapsed
     * @returns {{ root: Object|null, width: number, height: number }}
     */
    function calculate(repos, collapsedPaths = new Set()) {
        if (!repos || repos.length === 0) {
            return { root: null, width: 0, height: 0 };
        }

        // Find root (parent_path === null)
        const root = repos.find(r => r.parent_path === null);
        if (!root) {
            return { root: null, width: 0, height: 0 };
        }

        // Build children map keyed by path
        const childrenMap = {};
        for (const r of repos) {
            childrenMap[r.path] = [];
        }
        for (const r of repos) {
            if (r.parent_path !== null && childrenMap[r.parent_path]) {
                childrenMap[r.parent_path].push(r);
            }
        }

        // Sort children by name
        for (const key of Object.keys(childrenMap)) {
            childrenMap[key].sort((a, b) => a.name.localeCompare(b.name));
        }

        // Recursively compute layout
        const rootLayout = layoutSubtree(root, childrenMap, 0, collapsedPaths);

        // Get bounds
        const bounds = getBounds(rootLayout);

        // Shift so the tree starts at PADDING
        shiftSubtree(rootLayout, PADDING - bounds.minX, PADDING);

        const totalWidth = (bounds.maxX - bounds.minX) + 2 * PADDING;
        const totalHeight = bounds.maxY + 2 * PADDING;

        return { root: rootLayout, width: totalWidth, height: totalHeight };
    }

    function layoutSubtree(repo, childrenMap, depth, collapsedPaths) {
        const isCollapsed = collapsedPaths.has(repo.rel_path);
        const children = childrenMap[repo.path] || [];

        // Layout children (unless collapsed)
        const childLayouts = [];
        if (!isCollapsed) {
            for (const child of children) {
                childLayouts.push(layoutSubtree(child, childrenMap, depth + 1, collapsedPaths));
            }
        }

        const y = depth * (NODE_HEIGHT + V_GAP);

        if (childLayouts.length === 0) {
            return {
                repo,
                x: 0, y,
                width: NODE_WIDTH,
                height: NODE_HEIGHT,
                children: [],
                childCount: children.length,
                isCollapsed,
            };
        }

        // Total width of children subtrees
        let totalChildrenWidth = 0;
        for (const cl of childLayouts) {
            totalChildrenWidth += subtreeWidth(cl);
        }
        totalChildrenWidth += H_GAP * (childLayouts.length - 1);

        // Position children
        let currentX = -totalChildrenWidth / 2;
        for (const cl of childLayouts) {
            const sw = subtreeWidth(cl);
            const centerX = currentX + sw / 2;
            shiftSubtree(cl, centerX - cl.x, 0);
            currentX += sw + H_GAP;
        }

        return {
            repo,
            x: 0, y,
            width: NODE_WIDTH,
            height: NODE_HEIGHT,
            children: childLayouts,
            childCount: children.length,
            isCollapsed,
        };
    }

    function subtreeWidth(layout) {
        if (layout.children.length === 0) {
            return NODE_WIDTH;
        }
        let total = 0;
        for (const c of layout.children) {
            total += subtreeWidth(c);
        }
        total += H_GAP * (layout.children.length - 1);
        return Math.max(NODE_WIDTH, total);
    }

    function getBounds(layout) {
        let minX = layout.x - layout.width / 2;
        let maxX = layout.x + layout.width / 2;
        let maxY = layout.y + layout.height;

        for (const child of layout.children) {
            const cb = getBounds(child);
            minX = Math.min(minX, cb.minX);
            maxX = Math.max(maxX, cb.maxX);
            maxY = Math.max(maxY, cb.maxY);
        }

        return { minX, maxX, maxY };
    }

    function shiftSubtree(layout, dx, dy) {
        layout.x += dx;
        layout.y += dy;
        for (const child of layout.children) {
            shiftSubtree(child, dx, dy);
        }
    }

    return {
        calculate,
        NODE_WIDTH,
        NODE_HEIGHT,
        V_GAP,
    };
})();

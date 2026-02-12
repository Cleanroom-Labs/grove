/**
 * SVG graph rendering, zoom/pan, and node interaction.
 *
 * Renders repo nodes as SVG groups with status-colored headers,
 * branch info, and connecting edges. Supports smooth zoom/pan
 * via SVG transforms.
 */

const Graph = (() => {
    const STATUS_COLORS = {
        OK: '#4CAF50',
        UP_TO_DATE: '#4CAF50',
        PENDING: '#FFC107',
        BEHIND: '#FF9800',
        DIVERGED: '#F44336',
        UNCOMMITTED: '#F44336',
        DETACHED: '#9E9E9E',
        NO_REMOTE: '#9E9E9E',
    };

    const STATUS_TEXT = {
        OK: 'OK',
        UP_TO_DATE: 'Up to date',
        PENDING: 'Pending push',
        BEHIND: 'Behind remote',
        DIVERGED: 'Diverged',
        UNCOMMITTED: 'Uncommitted',
        DETACHED: 'Detached HEAD',
        NO_REMOTE: 'No remote',
    };

    let svg, group, container;
    let transform = { x: 0, y: 0, scale: 1 };
    let isPanning = false;
    let panStart = { x: 0, y: 0 };
    let selectedPath = null;
    let onSelectCallback = null;
    let onRightClickCallback = null;
    let collapsedPaths = new Set();
    let currentRepos = [];
    let layoutResult = null;

    const CORNER_RADIUS = 6;
    const HEADER_HEIGHT = 26;
    const PADDING = 10;
    const LINE_HEIGHT = 16;

    function init(callbacks) {
        svg = document.getElementById('graph-svg');
        group = document.getElementById('graph-group');
        container = document.getElementById('graph-container');

        onSelectCallback = callbacks.onSelect;
        onRightClickCallback = callbacks.onRightClick;

        // Zoom
        container.addEventListener('wheel', onWheel, { passive: false });

        // Pan
        container.addEventListener('mousedown', onMouseDown);
        window.addEventListener('mousemove', onMouseMove);
        window.addEventListener('mouseup', onMouseUp);

        // Click away to deselect
        svg.addEventListener('click', (e) => {
            if (e.target === svg || e.target.closest('#graph-group') === group && !e.target.closest('.node-group')) {
                selectedPath = null;
                render();
                if (onSelectCallback) onSelectCallback(null);
            }
        });
    }

    function setRepos(repos) {
        currentRepos = repos;
        layoutResult = Layout.calculate(repos, collapsedPaths);
        render();
    }

    function clearElement(el) {
        while (el.firstChild) {
            // Preserve <defs> in the SVG root
            if (el === group || el.firstChild.tagName !== 'defs') {
                el.removeChild(el.firstChild);
            } else {
                break;
            }
        }
    }

    function render() {
        clearElement(group);

        if (!layoutResult || !layoutResult.root) return;

        // Draw edges first
        drawEdges(layoutResult.root);

        // Draw nodes
        drawNodes(layoutResult.root);

        applyTransform();
    }

    function drawEdges(layout) {
        for (const child of layout.children) {
            const x1 = layout.x;
            const y1 = layout.y + layout.height;
            const x2 = child.x;
            const y2 = child.y;
            const cy = (y1 + y2) / 2;

            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', `M ${x1} ${y1} C ${x1} ${cy}, ${x2} ${cy}, ${x2} ${y2}`);
            path.setAttribute('class', 'edge-path');
            group.appendChild(path);

            drawEdges(child);
        }
    }

    function drawNodes(layout) {
        const repo = layout.repo;
        const w = layout.width;
        const h = layout.height;
        const left = layout.x - w / 2;
        const top = layout.y;

        const statusColor = STATUS_COLORS[repo.status] || '#9E9E9E';
        const statusText = STATUS_TEXT[repo.status] || repo.status;
        const isSelected = repo.path === selectedPath;

        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.setAttribute('class', 'node-group');
        g.setAttribute('data-path', repo.path);

        // Background rect
        const bg = createRect(left, top, w, h, CORNER_RADIUS, {
            fill: '#ffffff',
            stroke: repo.sync_group_color || (isSelected ? statusColor : '#cccccc'),
            'stroke-width': isSelected ? 2.5 : (repo.sync_group_color ? 2 : 1),
            class: 'node-bg',
        });
        g.appendChild(bg);

        // Header bar
        const header = createRect(left + 1, top + 1, w - 2, HEADER_HEIGHT, CORNER_RADIUS, {
            fill: statusColor,
            stroke: 'none',
        });
        header.setAttribute('rx', `${CORNER_RADIUS}`);
        header.setAttribute('ry', `${CORNER_RADIUS}`);
        g.appendChild(header);

        // Header clip (cover bottom rounded corners)
        const headerClip = createRect(left + 1, top + HEADER_HEIGHT - CORNER_RADIUS + 1, w - 2, CORNER_RADIUS, 0, {
            fill: statusColor,
            stroke: 'none',
        });
        g.appendChild(headerClip);

        // Repo name (in header)
        const name = repo.name;
        const nameText = createText(left + PADDING, top + HEADER_HEIGHT / 2 + 1, name, 'node-header-text');
        nameText.setAttribute('dominant-baseline', 'central');
        g.appendChild(nameText);

        // Detached HEAD hatch overlay
        if (repo.is_detached) {
            const hatch = createRect(left + 1, top + HEADER_HEIGHT + 1, w - 2, h - HEADER_HEIGHT - 2, 0, {
                fill: 'url(#detached-hatch)',
                stroke: 'none',
                'pointer-events': 'none',
            });
            g.appendChild(hatch);
        }

        // Info lines below header
        let yPos = top + HEADER_HEIGHT + PADDING + 2;

        // Branch (prominent)
        const branchLabel = repo.is_detached ? 'DETACHED HEAD' : repo.branch || '???';
        const branchText = createText(left + PADDING, yPos, branchLabel, 'node-branch-text');
        if (repo.is_detached) {
            branchText.setAttribute('fill', STATUS_COLORS.DETACHED);
        }
        g.appendChild(branchText);
        yPos += LINE_HEIGHT + 2;

        // Commit SHA
        const commitText = createText(left + PADDING, yPos, `${repo.commit}`, 'node-info-text');
        g.appendChild(commitText);
        yPos += LINE_HEIGHT;

        // Ahead/behind
        const abText = createText(left + PADDING, yPos, `\u2191${repo.ahead} \u2193${repo.behind}`, 'node-info-text');
        g.appendChild(abText);
        yPos += LINE_HEIGHT;

        // Status indicator
        const indicatorR = 4;
        const indicator = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        indicator.setAttribute('cx', left + PADDING + indicatorR);
        indicator.setAttribute('cy', yPos + indicatorR);
        indicator.setAttribute('r', indicatorR);
        indicator.setAttribute('fill', statusColor);
        g.appendChild(indicator);

        const statusLabel = createText(left + PADDING + indicatorR * 2 + 6, yPos + indicatorR, statusText, 'node-status-text');
        statusLabel.setAttribute('dominant-baseline', 'central');
        g.appendChild(statusLabel);

        // Collapse toggle (for non-leaf nodes)
        if (layout.childCount > 0) {
            const toggleText = layout.isCollapsed ? `+ (${layout.childCount})` : '\u2212';
            const toggle = createText(layout.x, top + h + 12, toggleText, 'collapse-toggle');
            toggle.setAttribute('text-anchor', 'middle');
            toggle.addEventListener('click', (e) => {
                e.stopPropagation();
                toggleCollapse(repo.rel_path);
            });
            g.appendChild(toggle);
        }

        // Sync group label
        if (repo.sync_group) {
            const sgText = createText(left + w - PADDING, top + h - 6, repo.sync_group, 'node-info-text');
            sgText.setAttribute('text-anchor', 'end');
            sgText.setAttribute('fill', repo.sync_group_color || '#999');
            sgText.setAttribute('font-size', '9');
            g.appendChild(sgText);
        }

        // Event handlers
        g.addEventListener('click', (e) => {
            e.stopPropagation();
            selectedPath = repo.path;
            layoutResult = Layout.calculate(currentRepos, collapsedPaths);
            render();
            if (onSelectCallback) onSelectCallback(repo);
        });

        g.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            e.stopPropagation();
            selectedPath = repo.path;
            layoutResult = Layout.calculate(currentRepos, collapsedPaths);
            render();
            if (onSelectCallback) onSelectCallback(repo);
            if (onRightClickCallback) onRightClickCallback(repo, e.clientX, e.clientY);
        });

        group.appendChild(g);

        // Recurse children
        for (const child of layout.children) {
            drawNodes(child);
        }
    }

    function toggleCollapse(relPath) {
        if (collapsedPaths.has(relPath)) {
            collapsedPaths.delete(relPath);
        } else {
            collapsedPaths.add(relPath);
        }
        layoutResult = Layout.calculate(currentRepos, collapsedPaths);
        render();
    }

    function zoomToFit() {
        if (!layoutResult || !layoutResult.root) return;

        const containerRect = container.getBoundingClientRect();
        const cw = containerRect.width;
        const ch = containerRect.height;
        const tw = layoutResult.width;
        const th = layoutResult.height;

        if (tw === 0 || th === 0) return;

        const scaleX = cw / tw;
        const scaleY = ch / th;
        const scale = Math.min(scaleX, scaleY, 1.5) * 0.9;

        transform.scale = scale;
        transform.x = (cw - tw * scale) / 2;
        transform.y = (ch - th * scale) / 2;

        applyTransform();
    }

    function zoomIn() {
        transform.scale = Math.min(transform.scale * 1.2, 3);
        applyTransform();
    }

    function zoomOut() {
        transform.scale = Math.max(transform.scale / 1.2, 0.2);
        applyTransform();
    }

    function applyTransform() {
        group.setAttribute('transform',
            `translate(${transform.x}, ${transform.y}) scale(${transform.scale})`);
    }

    // --- Helpers ---

    function createRect(x, y, w, h, r, attrs) {
        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('x', x);
        rect.setAttribute('y', y);
        rect.setAttribute('width', w);
        rect.setAttribute('height', h);
        rect.setAttribute('rx', r);
        rect.setAttribute('ry', r);
        for (const [k, v] of Object.entries(attrs)) {
            rect.setAttribute(k, v);
        }
        return rect;
    }

    function createText(x, y, content, className) {
        const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        text.setAttribute('x', x);
        text.setAttribute('y', y);
        text.setAttribute('class', className);
        text.textContent = content;
        return text;
    }

    // --- Zoom/Pan handlers ---

    function onWheel(e) {
        e.preventDefault();
        const factor = e.deltaY > 0 ? 0.92 : 1.08;
        const newScale = Math.max(0.2, Math.min(3, transform.scale * factor));

        const rect = container.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;

        // Zoom toward cursor
        const ratio = newScale / transform.scale;
        transform.x = mx - (mx - transform.x) * ratio;
        transform.y = my - (my - transform.y) * ratio;
        transform.scale = newScale;

        applyTransform();
    }

    function onMouseDown(e) {
        if (e.button !== 0) return;
        // Only pan if clicking on background
        if (e.target === svg || e.target === container ||
            (e.target.closest && !e.target.closest('.node-group'))) {
            isPanning = true;
            panStart = { x: e.clientX, y: e.clientY };
            container.classList.add('panning');
        }
    }

    function onMouseMove(e) {
        if (!isPanning) return;
        const dx = e.clientX - panStart.x;
        const dy = e.clientY - panStart.y;
        transform.x += dx;
        transform.y += dy;
        panStart = { x: e.clientX, y: e.clientY };
        applyTransform();
    }

    function onMouseUp() {
        isPanning = false;
        container.classList.remove('panning');
    }

    return {
        init,
        setRepos,
        render,
        zoomToFit,
        zoomIn,
        zoomOut,
    };
})();

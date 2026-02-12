/**
 * Application bootstrap, state management, and auto-refresh.
 *
 * Initializes all modules, loads initial data, and manages
 * the auto-refresh polling loop.
 */

const App = (() => {
    let autoRefreshInterval = null;
    let lastDataHash = null;

    function init() {
        // Initialize modules
        Graph.init({
            onSelect: onNodeSelect,
            onRightClick: (repo, x, y) => Actions.showContextMenu(repo, x, y),
        });

        Actions.init({
            onActionComplete: refresh,
        });

        Worktree.init({
            onSwitch: (repos) => Graph.setRepos(repos),
        });

        // Toolbar buttons
        document.getElementById('btn-refresh').addEventListener('click', refresh);
        document.getElementById('btn-zoom-fit').addEventListener('click', () => Graph.zoomToFit());
        document.getElementById('btn-zoom-in').addEventListener('click', () => Graph.zoomIn());
        document.getElementById('btn-zoom-out').addEventListener('click', () => Graph.zoomOut());

        // Auto-refresh toggle
        const checkbox = document.getElementById('auto-refresh-checkbox');
        checkbox.addEventListener('change', () => {
            if (checkbox.checked) {
                startAutoRefresh();
            } else {
                stopAutoRefresh();
            }
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'r' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                refresh();
            }
            if (e.key === 'Escape') {
                Actions.hideContextMenu();
            }
        });

        // Load initial data
        loadData();
    }

    async function loadData() {
        setStatus('Loading repositories...');

        try {
            const response = await fetch('/api/repos');
            const data = await response.json();

            if (data.repos) {
                Graph.setRepos(data.repos);

                // Set tab title to "Grove - <project name>"
                if (data.repo_root) {
                    const projectName = data.repo_root.split('/').pop();
                    document.title = `Grove - ${projectName}`;
                }

                // Zoom to fit on initial load
                requestAnimationFrame(() => Graph.zoomToFit());

                setStatus(`Loaded ${data.repos.length} repositories`);
                lastDataHash = hashData(data);
            } else {
                setStatus('No repositories found');
            }
        } catch (err) {
            setStatus(`Error loading: ${err.message}`);
        }

        // Load worktree tabs
        Worktree.loadWorktrees();
    }

    async function refresh() {
        setStatus('Refreshing...');

        try {
            // Tell server to reload
            await fetch('/api/action/refresh', { method: 'POST' });

            // Fetch fresh data
            const response = await fetch('/api/repos');
            const data = await response.json();

            if (data.repos) {
                Graph.setRepos(data.repos);
                setStatus(`Refreshed ${data.repos.length} repositories`);
                lastDataHash = hashData(data);
            }
        } catch (err) {
            setStatus(`Refresh error: ${err.message}`);
        }

        // Also refresh worktree tabs
        Worktree.loadWorktrees();
    }

    function onNodeSelect(repo) {
        const panel = document.getElementById('details-content');

        if (!repo) {
            panel.textContent = 'Select a repository to view details';
            return;
        }

        // Clear existing content
        while (panel.firstChild) {
            panel.removeChild(panel.firstChild);
        }

        const name = document.createElement('div');
        name.className = 'detail-name';
        name.textContent = repo.name;
        panel.appendChild(name);

        const info = document.createElement('div');
        const details = [
            `Path: ${repo.rel_path}`,
            `Branch: ${repo.branch || 'detached HEAD'}`,
            `Commit: ${repo.commit}`,
            `Ahead: ${repo.ahead}, Behind: ${repo.behind}`,
            `Status: ${repo.status}`,
        ];
        if (repo.sync_group) details.push(`Sync group: ${repo.sync_group}`);

        for (const detail of details) {
            const span = document.createElement('span');
            span.className = 'detail-row';
            span.textContent = detail;
            info.appendChild(span);
        }
        panel.appendChild(info);

        // Commit message
        if (repo.commit_message) {
            const msg = document.createElement('div');
            msg.className = 'detail-commit-message';
            msg.textContent = repo.commit_message;
            panel.appendChild(msg);
        }

        // Changed files (only shown when uncommitted)
        if (repo.changed_files && repo.changed_files.length > 0) {
            const section = document.createElement('div');
            section.className = 'detail-changes';

            const header = document.createElement('div');
            header.className = 'detail-changes-header';
            header.textContent = `Changes (${repo.changed_files.length})`;
            section.appendChild(header);

            const list = document.createElement('div');
            list.className = 'detail-changes-list';
            for (const file of repo.changed_files) {
                const row = document.createElement('div');
                row.className = 'detail-change-row';
                row.textContent = file;
                list.appendChild(row);
            }
            section.appendChild(list);
            panel.appendChild(section);
        }
    }

    function setStatus(message) {
        document.getElementById('status-text').textContent = message;
    }

    function startAutoRefresh() {
        stopAutoRefresh();
        autoRefreshInterval = setInterval(async () => {
            try {
                const response = await fetch('/api/repos');
                const data = await response.json();
                const hash = hashData(data);

                if (hash !== lastDataHash && data.repos) {
                    Graph.setRepos(data.repos);
                    lastDataHash = hash;
                    setStatus(`Auto-refreshed (${data.repos.length} repos)`);
                }
            } catch (_) {
                // Silent fail for auto-refresh
            }
        }, 10000);
    }

    function stopAutoRefresh() {
        if (autoRefreshInterval) {
            clearInterval(autoRefreshInterval);
            autoRefreshInterval = null;
        }
    }

    function hashData(data) {
        // Simple hash based on repo statuses and commits
        if (!data.repos) return '';
        return data.repos.map(r => `${r.path}:${r.status}:${r.commit}:${r.branch}`).join('|');
    }

    // Start the app when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    return {
        setStatus,
        refresh,
    };
})();

/**
 * Worktree tabs and comparison view.
 *
 * Provides a tab bar for switching between worktrees and a
 * side-by-side comparison table showing submodule differences.
 */

const Worktree = (() => {
    let tabContainer;
    let compareBtn;
    let compareView;
    let compareModal;
    let graphContainer;
    let worktrees = [];
    let activeWorktreePath = null;
    let onSwitchCallback = null;

    function init(callbacks) {
        onSwitchCallback = callbacks.onSwitch;

        tabContainer = document.getElementById('worktree-tabs');
        compareBtn = document.getElementById('btn-compare');
        compareView = document.getElementById('compare-view');
        compareModal = document.getElementById('compare-modal');
        graphContainer = document.getElementById('graph-container');

        compareBtn.addEventListener('click', showCompareModal);

        document.getElementById('btn-close-compare').addEventListener('click', closeCompare);
        document.getElementById('btn-run-compare').addEventListener('click', runCompare);
        document.getElementById('btn-cancel-compare').addEventListener('click', () => {
            compareModal.classList.add('hidden');
        });
        compareModal.querySelector('.modal-close').addEventListener('click', () => {
            compareModal.classList.add('hidden');
        });
        compareModal.addEventListener('click', (e) => {
            if (e.target === compareModal) compareModal.classList.add('hidden');
        });
    }

    async function loadWorktrees() {
        try {
            const response = await fetch('/api/worktrees');
            const data = await response.json();
            worktrees = data.worktrees || [];

            // Set initial active worktree
            if (!activeWorktreePath && worktrees.length > 0) {
                const current = worktrees.find(w => w.is_current);
                activeWorktreePath = current ? current.path : worktrees[0].path;
            }

            renderTabs();
        } catch (err) {
            console.error('Failed to load worktrees:', err);
        }
    }

    function renderTabs() {
        // Clear tabs using safe DOM method
        while (tabContainer.firstChild) {
            tabContainer.removeChild(tabContainer.firstChild);
        }

        if (worktrees.length <= 1) {
            // Single worktree - hide the bar elements
            compareBtn.classList.add('hidden');
            if (worktrees.length === 1) {
                const tab = createTab(worktrees[0], true);
                tabContainer.appendChild(tab);
            }
            return;
        }

        compareBtn.classList.remove('hidden');

        for (const wt of worktrees) {
            const isActive = wt.path === activeWorktreePath;
            const tab = createTab(wt, isActive);
            tabContainer.appendChild(tab);
        }
    }

    function createTab(wt, isActive) {
        const tab = document.createElement('button');
        tab.className = 'worktree-tab' + (isActive ? ' active' : '');

        const label = document.createElement('span');
        label.textContent = wt.branch || 'detached';
        tab.appendChild(label);

        if (wt.diff_count > 0) {
            const badge = document.createElement('span');
            badge.className = 'diff-badge';
            badge.textContent = wt.diff_count;
            tab.appendChild(badge);
        }

        tab.addEventListener('click', () => switchWorktree(wt.path));
        return tab;
    }

    async function switchWorktree(path) {
        if (path === activeWorktreePath) return;

        activeWorktreePath = path;
        renderTabs();

        // Close comparison view if open
        closeCompare();

        App.setStatus('Loading worktree...');

        try {
            const response = await fetch(`/api/worktree?path=${encodeURIComponent(path)}`);
            const data = await response.json();

            if (data.repos) {
                if (onSwitchCallback) onSwitchCallback(data.repos);
                App.setStatus(`Loaded ${data.repos.length} repositories`);
            } else {
                App.setStatus(`Error: ${data.error || 'Unknown error'}`);
            }
        } catch (err) {
            App.setStatus(`Error loading worktree: ${err.message}`);
        }
    }

    function showCompareModal() {
        if (worktrees.length < 2) return;

        const baseSelect = document.getElementById('compare-base');
        const otherSelect = document.getElementById('compare-other');

        // Clear selects using safe DOM methods
        while (baseSelect.firstChild) {
            baseSelect.removeChild(baseSelect.firstChild);
        }
        while (otherSelect.firstChild) {
            otherSelect.removeChild(otherSelect.firstChild);
        }

        for (const wt of worktrees) {
            const label = `${wt.branch || 'detached'} (${wt.path})`;

            const opt1 = document.createElement('option');
            opt1.value = wt.path;
            opt1.textContent = label;
            baseSelect.appendChild(opt1);

            const opt2 = document.createElement('option');
            opt2.value = wt.path;
            opt2.textContent = label;
            otherSelect.appendChild(opt2);
        }

        // Default: first worktree as base, second as other
        baseSelect.value = worktrees[0].path;
        if (worktrees.length > 1) {
            otherSelect.value = worktrees[1].path;
        }

        compareModal.classList.remove('hidden');
    }

    async function runCompare() {
        const basePath = document.getElementById('compare-base').value;
        const otherPath = document.getElementById('compare-other').value;

        if (basePath === otherPath) {
            App.setStatus('Cannot compare a worktree with itself');
            return;
        }

        compareModal.classList.add('hidden');
        App.setStatus('Comparing worktrees...');

        try {
            const url = `/api/compare?base=${encodeURIComponent(basePath)}&other=${encodeURIComponent(otherPath)}`;
            const response = await fetch(url);
            const data = await response.json();

            if (data.error) {
                App.setStatus(`Error: ${data.error}`);
                return;
            }

            showCompareView(data);
            App.setStatus('Comparison complete');
        } catch (err) {
            App.setStatus(`Error comparing: ${err.message}`);
        }
    }

    function showCompareView(data) {
        graphContainer.classList.add('hidden');
        compareView.classList.remove('hidden');

        const title = document.getElementById('compare-title');
        title.textContent = `Comparing: ${data.base_branch || '?'} vs ${data.other_branch || '?'}`;

        const content = document.getElementById('compare-content');
        // Clear content using safe DOM methods
        while (content.firstChild) {
            content.removeChild(content.firstChild);
        }

        const table = document.createElement('table');

        // Header
        const thead = document.createElement('thead');
        const headerRow = document.createElement('tr');
        for (const text of ['Submodule', `Base (${data.base_branch || '?'})`, `Other (${data.other_branch || '?'})`, 'Status']) {
            const th = document.createElement('th');
            th.textContent = text;
            headerRow.appendChild(th);
        }
        thead.appendChild(headerRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');

        // Different items first (most interesting)
        for (const item of data.different || []) {
            const row = document.createElement('tr');
            row.className = 'compare-diff';

            addCell(row, item.rel_path);
            addCell(row, `${item.base.branch || 'detached'} @ ${item.base.commit}`);
            addCell(row, `${item.other.branch || 'detached'} @ ${item.other.commit}`);
            addCell(row, 'Different');

            tbody.appendChild(row);
        }

        // Only in base
        for (const item of data.only_base || []) {
            const row = document.createElement('tr');
            row.className = 'compare-only';

            addCell(row, item.rel_path);
            addCell(row, `${item.branch || 'detached'} @ ${item.commit}`);
            addCell(row, '\u2014');
            addCell(row, 'Only in base');

            tbody.appendChild(row);
        }

        // Only in other
        for (const item of data.only_other || []) {
            const row = document.createElement('tr');
            row.className = 'compare-only';

            addCell(row, item.rel_path);
            addCell(row, '\u2014');
            addCell(row, `${item.branch || 'detached'} @ ${item.commit}`);
            addCell(row, 'Only in other');

            tbody.appendChild(row);
        }

        // Same items last
        for (const item of data.same || []) {
            const row = document.createElement('tr');
            row.className = 'compare-same';

            addCell(row, item.rel_path);
            addCell(row, `${item.branch || 'detached'} @ ${item.commit}`);
            addCell(row, `${item.branch || 'detached'} @ ${item.commit}`);
            addCell(row, 'Same');

            tbody.appendChild(row);
        }

        table.appendChild(tbody);
        content.appendChild(table);
    }

    function addCell(row, text) {
        const td = document.createElement('td');
        td.textContent = text;
        row.appendChild(td);
    }

    function closeCompare() {
        compareView.classList.add('hidden');
        graphContainer.classList.remove('hidden');
    }

    return {
        init,
        loadWorktrees,
    };
})();

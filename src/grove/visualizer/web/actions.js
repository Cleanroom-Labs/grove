/**
 * Context menu and branch picker for git actions.
 *
 * Handles right-click context menus, branch checkout dialogs,
 * and POST requests to the server for git operations.
 */

const Actions = (() => {
    let contextMenu;
    let branchModal;
    let onActionComplete = null;
    let currentCheckoutRepo = null;
    let selectedBranch = null;

    function init(callbacks) {
        onActionComplete = callbacks.onActionComplete;
        contextMenu = document.getElementById('context-menu');
        branchModal = document.getElementById('branch-modal');

        // Close context menu on click anywhere
        document.addEventListener('click', () => hideContextMenu());
        document.addEventListener('contextmenu', (e) => {
            // Allow right-click on nodes (handled by Graph), close menu otherwise
            if (!e.target.closest('.node-group')) {
                hideContextMenu();
            }
        });

        // Branch modal buttons
        document.getElementById('btn-checkout').addEventListener('click', doCheckout);
        document.getElementById('btn-cancel-checkout').addEventListener('click', () => {
            branchModal.classList.add('hidden');
        });
        branchModal.querySelector('.modal-close').addEventListener('click', () => {
            branchModal.classList.add('hidden');
        });

        // Close modal on backdrop click
        branchModal.addEventListener('click', (e) => {
            if (e.target === branchModal) branchModal.classList.add('hidden');
        });
    }

    function showContextMenu(repo, x, y) {
        const items = contextMenu.querySelector('.context-menu-items');
        // Clear previous items using safe DOM method
        while (items.firstChild) {
            items.removeChild(items.firstChild);
        }

        addMenuItem(items, `Fetch ${repo.name}`, () => postAction('/api/action/fetch', { path: repo.path }));
        addMenuItem(items, `Push ${repo.name}`, () => postAction('/api/action/push', { path: repo.path }));
        addMenuItem(items, 'Checkout Branch...', () => showBranchPicker(repo));
        addSeparator(items);
        addMenuItem(items, 'Fetch All', () => postAction('/api/action/fetch-all', {}));
        addMenuItem(items, 'Push All', () => postAction('/api/action/push-all', {}));

        // Position the menu
        contextMenu.style.left = `${x}px`;
        contextMenu.style.top = `${y}px`;
        contextMenu.classList.remove('hidden');

        // Adjust if menu goes off-screen
        requestAnimationFrame(() => {
            const rect = contextMenu.getBoundingClientRect();
            if (rect.right > window.innerWidth) {
                contextMenu.style.left = `${x - rect.width}px`;
            }
            if (rect.bottom > window.innerHeight) {
                contextMenu.style.top = `${y - rect.height}px`;
            }
        });
    }

    function hideContextMenu() {
        contextMenu.classList.add('hidden');
    }

    function addMenuItem(container, label, onClick) {
        const item = document.createElement('div');
        item.className = 'context-menu-item';
        item.textContent = label;
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            hideContextMenu();
            onClick();
        });
        container.appendChild(item);
    }

    function addSeparator(container) {
        const sep = document.createElement('div');
        sep.className = 'context-menu-separator';
        container.appendChild(sep);
    }

    function showBranchPicker(repo) {
        currentCheckoutRepo = repo;
        selectedBranch = null;

        const localList = document.getElementById('local-branches');
        const remoteList = document.getElementById('remote-branches');
        const checkoutBtn = document.getElementById('btn-checkout');

        // Clear lists using safe DOM methods
        while (localList.firstChild) {
            localList.removeChild(localList.firstChild);
        }
        while (remoteList.firstChild) {
            remoteList.removeChild(remoteList.firstChild);
        }
        checkoutBtn.disabled = true;

        // Populate local branches
        const localBranches = repo.local_branches || [];
        for (const branch of localBranches) {
            const item = document.createElement('div');
            item.className = 'branch-item' + (branch === repo.branch ? ' current' : '');
            item.textContent = branch;
            item.addEventListener('click', () => selectBranch(branch, item, localList, remoteList));
            localList.appendChild(item);
        }

        // Populate remote branches (exclude those already local)
        const remoteBranches = (repo.remote_branches || []).filter(b => !localBranches.includes(b));
        for (const branch of remoteBranches) {
            const item = document.createElement('div');
            item.className = 'branch-item';
            item.textContent = branch;
            item.addEventListener('click', () => selectBranch(branch, item, localList, remoteList));
            remoteList.appendChild(item);
        }

        branchModal.classList.remove('hidden');
    }

    function selectBranch(branch, item, localList, remoteList) {
        // Deselect all
        for (const el of localList.querySelectorAll('.branch-item')) {
            el.classList.remove('selected');
        }
        for (const el of remoteList.querySelectorAll('.branch-item')) {
            el.classList.remove('selected');
        }

        // Select this one
        item.classList.add('selected');
        selectedBranch = branch;
        document.getElementById('btn-checkout').disabled = false;
    }

    function doCheckout() {
        if (!currentCheckoutRepo || !selectedBranch) return;
        branchModal.classList.add('hidden');
        postAction('/api/action/checkout', {
            path: currentCheckoutRepo.path,
            branch: selectedBranch,
        });
    }

    async function postAction(url, body) {
        App.setStatus(`Working...`);

        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const result = await response.json();

            if (result.ok) {
                App.setStatus(result.error || 'Done');
            } else {
                App.setStatus(`Error: ${result.error}`);
            }
        } catch (err) {
            App.setStatus(`Network error: ${err.message}`);
        }

        // Always refresh after an action
        if (onActionComplete) onActionComplete();
    }

    return {
        init,
        showContextMenu,
        hideContextMenu,
    };
})();

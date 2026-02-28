"""Tests for grove.topology."""

from pathlib import Path

from grove.topology import (
    DEFAULT_MAX_SNAPSHOTS,
    SubmoduleEntry,
    TopologyCache,
    TopologySnapshot,
    build_entries,
    compute_topology_hash,
    diff_snapshots,
    _is_relative_url,
    _resolve_relative_url,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    rel_path="sub",
    parent_rel_path=".",
    url="git@github.com:Org/sub.git",
    relative_url=None,
    commit="abc1234",
) -> SubmoduleEntry:
    return SubmoduleEntry(
        rel_path=rel_path,
        parent_rel_path=parent_rel_path,
        url=url,
        relative_url=relative_url,
        commit=commit,
    )


def _snap(
    root_commit="aaa",
    entries=None,
    timestamp="2026-01-01T00:00:00",
) -> TopologySnapshot:
    entries = entries or []
    return TopologySnapshot(
        root_commit=root_commit,
        timestamp=timestamp,
        topology_hash=compute_topology_hash(entries),
        entries=entries,
    )


# ---------------------------------------------------------------------------
# SubmoduleEntry
# ---------------------------------------------------------------------------


class TestSubmoduleEntry:
    def test_structure_key_excludes_commit(self):
        e = _entry(commit="xyz")
        assert e.structure_key() == ("sub", ".", "git@github.com:Org/sub.git")

    def test_structure_key_includes_parent(self):
        e = _entry(parent_rel_path="parent")
        assert e.structure_key()[1] == "parent"

    def test_relative_url_stored(self):
        e = _entry(relative_url="../sub.git")
        assert e.relative_url == "../sub.git"

    def test_relative_url_none_for_absolute(self):
        e = _entry()
        assert e.relative_url is None


# ---------------------------------------------------------------------------
# compute_topology_hash
# ---------------------------------------------------------------------------


class TestTopologyHash:
    def test_deterministic(self):
        entries = [_entry(rel_path="a"), _entry(rel_path="b")]
        h1 = compute_topology_hash(entries)
        h2 = compute_topology_hash(entries)
        assert h1 == h2

    def test_order_independent(self):
        a = _entry(rel_path="a")
        b = _entry(rel_path="b")
        assert compute_topology_hash([a, b]) == compute_topology_hash([b, a])

    def test_different_commits_same_hash(self):
        """Commit changes should NOT affect the topology hash."""
        e1 = [_entry(commit="111")]
        e2 = [_entry(commit="222")]
        assert compute_topology_hash(e1) == compute_topology_hash(e2)

    def test_different_url_different_hash(self):
        e1 = [_entry(url="git@github.com:Org/a.git")]
        e2 = [_entry(url="git@github.com:Org/b.git")]
        assert compute_topology_hash(e1) != compute_topology_hash(e2)

    def test_different_parent_different_hash(self):
        e1 = [_entry(parent_rel_path=".")]
        e2 = [_entry(parent_rel_path="parent")]
        assert compute_topology_hash(e1) != compute_topology_hash(e2)

    def test_empty_entries(self):
        h = compute_topology_hash([])
        assert isinstance(h, str) and len(h) == 64


# ---------------------------------------------------------------------------
# diff_snapshots
# ---------------------------------------------------------------------------


class TestDiffSnapshots:
    def test_identical_snapshots(self):
        entries = [_entry()]
        s1 = _snap(entries=entries)
        s2 = _snap(entries=list(entries))
        d = diff_snapshots(s1, s2)
        assert d.is_empty

    def test_added(self):
        s1 = _snap(entries=[])
        s2 = _snap(entries=[_entry(rel_path="new")])
        d = diff_snapshots(s1, s2)
        assert len(d.added) == 1
        assert d.added[0].rel_path == "new"
        assert d.removed == []

    def test_removed(self):
        s1 = _snap(entries=[_entry(rel_path="old")])
        s2 = _snap(entries=[])
        d = diff_snapshots(s1, s2)
        assert len(d.removed) == 1
        assert d.removed[0].rel_path == "old"
        assert d.added == []

    def test_changed_url(self):
        old = _entry(url="git@github.com:Org/old.git")
        new = _entry(url="git@github.com:Org/new.git")
        d = diff_snapshots(_snap(entries=[old]), _snap(entries=[new]))
        assert len(d.changed_url) == 1
        assert d.changed_url[0][0].url == "git@github.com:Org/old.git"
        assert d.changed_url[0][1].url == "git@github.com:Org/new.git"

    def test_changed_relative_url(self):
        old = _entry(relative_url="../old.git")
        new = _entry(relative_url="../new.git")
        d = diff_snapshots(_snap(entries=[old]), _snap(entries=[new]))
        assert len(d.changed_relative_url) == 1

    def test_relative_url_added(self):
        """Going from absolute-only to having a relative URL."""
        old = _entry(relative_url=None)
        new = _entry(relative_url="../sub.git")
        d = diff_snapshots(_snap(entries=[old]), _snap(entries=[new]))
        assert len(d.changed_relative_url) == 1

    def test_changed_commit(self):
        old = _entry(commit="aaa")
        new = _entry(commit="bbb")
        d = diff_snapshots(_snap(entries=[old]), _snap(entries=[new]))
        assert len(d.changed_commit) == 1
        assert d.changed_commit[0][0].commit == "aaa"
        assert d.changed_commit[0][1].commit == "bbb"

    def test_reparented(self):
        old = _entry(parent_rel_path=".")
        new = _entry(parent_rel_path="parent")
        d = diff_snapshots(_snap(entries=[old]), _snap(entries=[new]))
        assert len(d.reparented) == 1

    def test_has_structural_changes(self):
        """Structural changes should be detected (not just commit diffs)."""
        old = _entry(url="git@github.com:Org/old.git")
        new = _entry(url="git@github.com:Org/new.git")
        d = diff_snapshots(_snap(entries=[old]), _snap(entries=[new]))
        assert d.has_structural_changes

    def test_commit_only_not_structural(self):
        """Commit-only changes should not count as structural."""
        old = _entry(commit="aaa")
        new = _entry(commit="bbb")
        d = diff_snapshots(_snap(entries=[old]), _snap(entries=[new]))
        assert not d.has_structural_changes
        assert not d.is_empty

    def test_multiple_changes(self):
        """Multiple types of changes in one diff."""
        s1 = _snap(
            entries=[
                _entry(rel_path="a", commit="111"),
                _entry(rel_path="b", url="git@github.com:Org/old-b.git"),
                _entry(rel_path="c"),
            ]
        )
        s2 = _snap(
            entries=[
                _entry(rel_path="a", commit="222"),
                _entry(rel_path="b", url="git@github.com:Org/new-b.git"),
                _entry(rel_path="d"),
            ]
        )
        d = diff_snapshots(s1, s2)
        assert len(d.changed_commit) == 1
        assert len(d.changed_url) == 1
        assert len(d.added) == 1
        assert d.added[0].rel_path == "d"
        assert len(d.removed) == 1
        assert d.removed[0].rel_path == "c"


# ---------------------------------------------------------------------------
# TopologyCache
# ---------------------------------------------------------------------------


class TestTopologyCacheCorruption:
    """Edge cases: corrupt or unexpected cache files."""

    def test_load_truncated_json(self, tmp_path: Path):
        cache_path = tmp_path / "topo.json"
        cache_path.write_text('{"snapshots": [{"root_commit": "abc"')
        cache = TopologyCache(cache_path)
        import json
        import pytest

        with pytest.raises(json.JSONDecodeError):
            cache.load()

    def test_load_missing_snapshots_key(self, tmp_path: Path):
        cache_path = tmp_path / "topo.json"
        cache_path.write_text('{"other": true}')
        cache = TopologyCache(cache_path)
        cache.load()
        assert cache.snapshots == []

    def test_load_empty_file(self, tmp_path: Path):
        cache_path = tmp_path / "topo.json"
        cache_path.write_text("")
        cache = TopologyCache(cache_path)
        import json
        import pytest

        with pytest.raises(json.JSONDecodeError):
            cache.load()

    def test_load_malformed_entry(self, tmp_path: Path):
        """An entry missing required fields should raise."""
        import json
        import pytest

        cache_path = tmp_path / "topo.json"
        cache_path.write_text(
            json.dumps(
                {
                    "snapshots": [
                        {
                            "root_commit": "abc",
                            "timestamp": "2026-01-01T00:00:00",
                            "topology_hash": "xyz",
                            "entries": [
                                {"rel_path": "sub"}
                            ],  # missing other required fields
                        }
                    ]
                }
            )
        )
        cache = TopologyCache(cache_path)
        with pytest.raises(TypeError):
            cache.load()


class TestTopologyCache:
    def test_load_empty(self, tmp_path: Path):
        cache = TopologyCache(tmp_path / "topo.json")
        cache.load()
        assert cache.snapshots == []

    def test_save_and_load(self, tmp_path: Path):
        cache_path = tmp_path / "topo.json"
        cache = TopologyCache(cache_path)
        snap = _snap(entries=[_entry()])
        cache.snapshots.append(snap)
        cache.save()

        cache2 = TopologyCache(cache_path)
        cache2.load()
        assert len(cache2.snapshots) == 1
        assert cache2.snapshots[0].root_commit == snap.root_commit
        assert cache2.snapshots[0].topology_hash == snap.topology_hash
        assert len(cache2.snapshots[0].entries) == 1
        e = cache2.snapshots[0].entries[0]
        assert e.rel_path == "sub"
        assert e.parent_rel_path == "."
        assert e.commit == "abc1234"

    def test_save_preserves_relative_url(self, tmp_path: Path):
        cache_path = tmp_path / "topo.json"
        cache = TopologyCache(cache_path)
        cache.snapshots.append(_snap(entries=[_entry(relative_url="../sub.git")]))
        cache.save()

        cache2 = TopologyCache(cache_path)
        cache2.load()
        assert cache2.snapshots[0].entries[0].relative_url == "../sub.git"

    def test_get_found(self, tmp_path: Path):
        cache = TopologyCache(tmp_path / "topo.json")
        cache.snapshots.append(_snap(root_commit="abc"))
        assert cache.get("abc") is not None
        assert cache.get("abc").root_commit == "abc"

    def test_get_not_found(self, tmp_path: Path):
        cache = TopologyCache(tmp_path / "topo.json")
        assert cache.get("nope") is None

    def test_compare(self, tmp_path: Path):
        cache = TopologyCache(tmp_path / "topo.json")
        cache.snapshots.append(_snap(root_commit="a", entries=[_entry(commit="111")]))
        cache.snapshots.append(_snap(root_commit="b", entries=[_entry(commit="222")]))
        d = cache.compare("a", "b")
        assert d is not None
        assert len(d.changed_commit) == 1

    def test_compare_missing_returns_none(self, tmp_path: Path):
        cache = TopologyCache(tmp_path / "topo.json")
        cache.snapshots.append(_snap(root_commit="a"))
        assert cache.compare("a", "missing") is None
        assert cache.compare("missing", "a") is None

    def test_prune(self, tmp_path: Path):
        cache = TopologyCache(tmp_path / "topo.json")
        for i in range(10):
            cache.snapshots.append(_snap(root_commit=str(i)))
        cache.prune(max_entries=5)
        assert len(cache.snapshots) == 5
        # Should keep the most recent (last added)
        assert cache.snapshots[-1].root_commit == "9"
        assert cache.snapshots[0].root_commit == "5"

    def test_prune_default_max(self, tmp_path: Path):
        cache = TopologyCache(tmp_path / "topo.json")
        assert DEFAULT_MAX_SNAPSHOTS == 500
        for i in range(505):
            cache.snapshots.append(_snap(root_commit=str(i)))
        cache.prune()
        assert len(cache.snapshots) == 500

    def test_prune_noop_when_under_limit(self, tmp_path: Path):
        cache = TopologyCache(tmp_path / "topo.json")
        cache.snapshots.append(_snap(root_commit="a"))
        cache.prune(max_entries=10)
        assert len(cache.snapshots) == 1

    def test_record_skips_duplicate(self, tmp_path: Path):
        cache = TopologyCache(tmp_path / "topo.json")
        cache.snapshots.append(_snap(root_commit="abc"))
        cache.record("abc", [], tmp_path)
        assert len(cache.snapshots) == 1

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        cache_path = tmp_path / "deep" / "nested" / "topo.json"
        cache = TopologyCache(cache_path)
        cache.snapshots.append(_snap())
        cache.save()
        assert cache_path.exists()


# ---------------------------------------------------------------------------
# _is_relative_url
# ---------------------------------------------------------------------------


class TestIsRelativeUrl:
    def test_dotdot(self):
        assert _is_relative_url("../foo.git") is True

    def test_dot(self):
        assert _is_relative_url("./foo.git") is True

    def test_ssh(self):
        assert _is_relative_url("git@github.com:Org/repo.git") is False

    def test_https(self):
        assert _is_relative_url("https://github.com/Org/repo.git") is False

    def test_bare_path(self):
        assert _is_relative_url("/tmp/repos/foo") is False


# ---------------------------------------------------------------------------
# _resolve_relative_url
# ---------------------------------------------------------------------------


class TestResolveRelativeUrl:
    def test_ssh_dotdot(self):
        result = _resolve_relative_url("git@github.com:Org/parent.git", "../child.git")
        assert result == "git@github.com:Org/child.git"

    def test_ssh_dotdot_multiple(self):
        result = _resolve_relative_url(
            "git@github.com:Org/Sub/parent.git", "../../other.git"
        )
        assert result == "git@github.com:Org/other.git"

    def test_https_dotdot(self):
        result = _resolve_relative_url(
            "https://github.com/Org/parent.git", "../child.git"
        )
        assert "child.git" in result

    def test_local_path(self):
        result = _resolve_relative_url("/tmp/repos/parent", "../child")
        assert result.endswith("child")


# ---------------------------------------------------------------------------
# build_entries (integration with real git repos)
# ---------------------------------------------------------------------------


class TestBuildEntries:
    def test_with_submodule_tree(self, tmp_submodule_tree: Path):
        """build_entries should produce entries for submodules with correct parent info."""
        from grove.repo_utils import discover_repos_from_gitmodules

        repos = discover_repos_from_gitmodules(tmp_submodule_tree)
        entries = build_entries(repos, tmp_submodule_tree)

        # Should have entries for technical-docs and technical-docs/common
        rel_paths = {e.rel_path for e in entries}
        assert "technical-docs" in rel_paths
        assert "technical-docs/common" in rel_paths

        # Check parent relationships
        by_path = {e.rel_path: e for e in entries}
        assert by_path["technical-docs"].parent_rel_path == "."
        assert by_path["technical-docs/common"].parent_rel_path == "technical-docs"

    def test_entries_have_commits(self, tmp_submodule_tree: Path):
        from grove.repo_utils import discover_repos_from_gitmodules

        repos = discover_repos_from_gitmodules(tmp_submodule_tree)
        entries = build_entries(repos, tmp_submodule_tree)

        for e in entries:
            assert e.commit != ""
            assert e.commit != "unknown"

    def test_entries_have_urls(self, tmp_submodule_tree: Path):
        from grove.repo_utils import discover_repos_from_gitmodules

        repos = discover_repos_from_gitmodules(tmp_submodule_tree)
        entries = build_entries(repos, tmp_submodule_tree)

        for e in entries:
            assert e.url != ""


# ---------------------------------------------------------------------------
# TopologyCache.record (integration)
# ---------------------------------------------------------------------------


class TestTopologyCacheRecord:
    def test_record_from_real_repos(self, tmp_submodule_tree: Path):
        from grove.repo_utils import discover_repos_from_gitmodules, run_git

        cache = TopologyCache(tmp_submodule_tree / ".git" / "topo.json")
        repos = discover_repos_from_gitmodules(tmp_submodule_tree)

        result = run_git(tmp_submodule_tree, "rev-parse", "--short", "HEAD")
        root_commit = result.stdout.strip()

        cache.record(root_commit, repos, tmp_submodule_tree)
        assert len(cache.snapshots) == 1

        snap = cache.get(root_commit)
        assert snap is not None
        assert len(snap.entries) == 2  # technical-docs and technical-docs/common
        assert snap.topology_hash

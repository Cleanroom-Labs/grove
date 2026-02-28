"""Tests for grove.filelock — file locking utilities."""

from __future__ import annotations

import json
import threading
from pathlib import Path


from grove.filelock import atomic_write_json, locked_open


class TestLockedOpen:
    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "a" / "b" / "file.txt"
        with locked_open(target, "w") as f:
            f.write("hello")
        assert target.read_text() == "hello"

    def test_read_mode(self, tmp_path: Path):
        target = tmp_path / "file.txt"
        target.write_text("contents")
        with locked_open(target, "r", shared=True) as f:
            assert f.read() == "contents"

    def test_append_mode(self, tmp_path: Path):
        target = tmp_path / "file.txt"
        target.write_text("line1\n")
        with locked_open(target, "a") as f:
            f.write("line2\n")
        assert target.read_text() == "line1\nline2\n"

    def test_shared_readers_concurrent(self, tmp_path: Path):
        target = tmp_path / "file.txt"
        target.write_text("data")
        results = []

        def reader():
            with locked_open(target, "r", shared=True) as f:
                results.append(f.read())

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert results == ["data"] * 4


class TestAtomicWriteJson:
    def test_basic_write(self, tmp_path: Path):
        target = tmp_path / "data.json"
        atomic_write_json(target, '{"key": "value"}\n')
        assert json.loads(target.read_text()) == {"key": "value"}

    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "nested" / "dir" / "data.json"
        atomic_write_json(target, '{"ok": true}\n')
        assert target.exists()
        assert json.loads(target.read_text()) == {"ok": True}

    def test_overwrites_existing(self, tmp_path: Path):
        target = tmp_path / "data.json"
        target.write_text('{"old": true}\n')
        atomic_write_json(target, '{"new": true}\n')
        assert json.loads(target.read_text()) == {"new": True}

    def test_concurrent_writes_produce_valid_json(self, tmp_path: Path):
        target = tmp_path / "data.json"
        errors = []

        def writer(n: int):
            try:
                for i in range(20):
                    data = json.dumps({"writer": n, "iteration": i})
                    atomic_write_json(target, data + "\n")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        # Final file must be valid JSON
        data = json.loads(target.read_text())
        assert "writer" in data
        assert "iteration" in data

    def test_no_leftover_tmp_files(self, tmp_path: Path):
        target = tmp_path / "data.json"
        atomic_write_json(target, '{"ok": true}\n')
        # Only data.json and data.json.lock should exist
        files = sorted(f.name for f in tmp_path.iterdir())
        assert "data.json" in files
        assert not any(f.endswith(".tmp") for f in files)


class TestConcurrentTopologySave:
    """Two threads saving TopologyCache simultaneously should produce valid JSON."""

    def test_concurrent_cache_saves(self, tmp_path: Path):
        from grove.topology import (
            TopologyCache,
            TopologySnapshot,
            SubmoduleEntry,
            compute_topology_hash,
        )

        cache_path = tmp_path / "topo.json"
        errors = []

        def saver(n: int):
            try:
                for i in range(10):
                    cache = TopologyCache(cache_path)
                    entries = [
                        SubmoduleEntry(
                            rel_path=f"sub-{n}",
                            parent_rel_path=".",
                            url=f"git@github.com:Org/sub-{n}.git",
                            relative_url=None,
                            commit=f"{n:03d}{i:04d}",
                        )
                    ]
                    cache.snapshots = [
                        TopologySnapshot(
                            root_commit=f"{n:03d}{i:04d}",
                            timestamp="2026-01-01T00:00:00",
                            topology_hash=compute_topology_hash(entries),
                            entries=entries,
                        )
                    ]
                    cache.save()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=saver, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        # Final file must be valid JSON
        data = json.loads(cache_path.read_text())
        assert "snapshots" in data


class TestConcurrentJournalLog:
    """Two threads calling _log() simultaneously should both write their lines."""

    def test_concurrent_log_writes(self, tmp_path: Path):
        from grove.worktree_merge import _log

        journal = tmp_path / "merge.log"
        errors = []

        def logger(n: int):
            try:
                for i in range(20):
                    _log(journal, f"thread-{n}-iter-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=logger, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        content = journal.read_text()
        lines = [line for line in content.strip().split("\n") if line]
        # 4 threads × 20 iterations = 80 lines
        assert len(lines) == 80

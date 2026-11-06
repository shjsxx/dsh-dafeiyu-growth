"""Explicit, local backups for Yunlan's bounded native state.

This module deliberately does not run automatically. A hidden rolling backup of
memory.json would resurrect records the user asked to forget. Backups are only
created when the user runs the CLI (or a future explicit UI action), and every
restore first creates a safety snapshot.

Only the known, bounded state files are copied. Models, keys, logs, screenshots
and arbitrary files are never included.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

VERSION = 1
STATE_FILES = (
    "profile.json",
    "growth.json",
    "memory.json",
    "memory-archive.json",
    "relationship.json",
    "personality.json",
    "persona-history.json",
    "research-scheduler.json",
    "memory-health.json",
    "persona-health.json",
    "continuity.json",
    "companion_state.json",
    "behavior.json",
    "action_preferences.json",
    "neural.json",
    "emotion.json",
    "training.json",
    "knowledge.json",
    "learning.json",
    "online.json",
    "companion.json",
    "embedding_rerank.json",
    "pending-diary.jsonl",
    "sleep.json",
    "layout.json",
)
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_SNAPSHOTS = 20


class BackupError(RuntimeError):
    pass


def default_data_dir() -> Path:
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_child(root: Path, name: str) -> Path:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise BackupError("unsafe state filename")
    target = (root / name).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as error:
        raise BackupError("state path escaped the data directory") from error
    return target


class StateBackup:
    def __init__(self, data_dir=None, backup_dir=None, max_snapshots=MAX_SNAPSHOTS, clock=None):
        self.data_dir = Path(data_dir).expanduser().resolve() if data_dir else default_data_dir().resolve()
        self.backup_dir = (Path(backup_dir).expanduser().resolve()
                           if backup_dir else self.data_dir / "backups")
        self.max_snapshots = max(1, min(100, int(max_snapshots)))
        self.clock = clock or (lambda: time.time())

    # ---- backup ------------------------------------------------------
    def state_files(self):
        result = []
        for name in STATE_FILES:
            path = _safe_child(self.data_dir, name)
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                size = path.stat().st_size
            except OSError as error:
                raise BackupError(f"state file is unreadable: {name}") from error
            if size > MAX_FILE_BYTES:
                raise BackupError(f"state file is too large for a bounded backup: {name}")
            result.append(path)
        return result

    def create(self, label="manual", now=None):
        """Create one immutable snapshot, or return None when there is no state."""
        files = self.state_files()
        if not files:
            return None
        stamp = int(self.clock() if now is None else now)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S", time.localtime(stamp)) + f"-{os.getpid()}"
        final = self.backup_dir / name
        suffix = 1
        while final.exists():
            suffix += 1
            final = self.backup_dir / f"{name}-{suffix}"
        temporary = self.backup_dir / f".{name}.{os.getpid()}.tmp"
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        temporary.mkdir(parents=True)
        manifest = {"version": VERSION, "createdAt": stamp, "label": str(label)[:80], "files": {}}
        try:
            for source in files:
                data = source.read_bytes()
                target = temporary / source.name
                target.write_bytes(data)
                manifest["files"][source.name] = {
                    "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                encoding="utf-8")
            os.replace(temporary, final)
        except (OSError, ValueError):
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        self.prune()
        return final

    def list(self):
        if not self.backup_dir.is_dir():
            return []
        rows = []
        for path in self.backup_dir.iterdir():
            if not path.is_dir() or path.is_symlink():
                continue
            try:
                manifest = self._manifest(path)
            except BackupError:
                continue
            rows.append({
                "path": str(path), "name": path.name, "createdAt": manifest["createdAt"],
                "label": manifest.get("label", ""), "files": sorted(manifest["files"]),
            })
        return sorted(rows, key=lambda row: (row["createdAt"], row["name"]), reverse=True)

    def prune(self):
        snapshots = self.list()
        removed = []
        for row in snapshots[self.max_snapshots:]:
            path = Path(row["path"])
            if path.parent.resolve() != self.backup_dir.resolve():
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed.append(row["name"])
        return removed

    # ---- verify ------------------------------------------------------
    @staticmethod
    def _manifest(snapshot):
        snapshot = Path(snapshot).resolve()
        manifest_path = snapshot / "manifest.json"
        if snapshot.is_symlink() or not snapshot.is_dir() or not manifest_path.is_file():
            raise BackupError("snapshot has no readable manifest")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as error:
            raise BackupError("snapshot manifest is corrupt") from error
        if not isinstance(manifest, dict) or manifest.get("version") != VERSION:
            raise BackupError("unsupported snapshot version")
        if not isinstance(manifest.get("files"), dict) or not manifest["files"]:
            raise BackupError("snapshot contains no state files")
        return manifest

    def verify(self, snapshot):
        snapshot = Path(snapshot).expanduser().resolve()
        manifest = self._manifest(snapshot)
        problems = []
        for name, expected in manifest["files"].items():
            if name not in STATE_FILES or not isinstance(expected, dict):
                problems.append(f"unexpected file: {name}")
                continue
            path = _safe_child(snapshot, name)
            if not path.is_file() or path.is_symlink():
                problems.append(f"missing file: {name}")
                continue
            try:
                size = path.stat().st_size
                digest = _sha256(path)
            except OSError:
                problems.append(f"unreadable file: {name}")
                continue
            if size != expected.get("size") or digest != expected.get("sha256"):
                problems.append(f"hash mismatch: {name}")
        if problems:
            raise BackupError("; ".join(problems))
        return manifest

    # ---- restore -----------------------------------------------------
    def restore(self, snapshot, force=False):
        """Restore only manifest files. Existing unrelated files are untouched."""
        snapshot = Path(snapshot).expanduser().resolve()
        manifest = self.verify(snapshot)
        if not force:
            # A restore must never destroy the current state without a copy.
            safety = self.create("before-restore")
            if safety is None and self.state_files():
                raise BackupError("could not create a safety snapshot")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        restored = []
        for name in sorted(manifest["files"]):
            source = _safe_child(snapshot, name)
            target = _safe_child(self.data_dir, name)
            if target.exists() and target.is_symlink():
                raise BackupError(f"refusing to overwrite symlink: {name}")
            temporary = target.with_name(target.name + f".{os.getpid()}.restore.tmp")
            try:
                data = source.read_bytes()
                if not data or len(data) > MAX_FILE_BYTES:
                    raise BackupError(f"snapshot file is empty or too large: {name}")
                temporary.write_bytes(data)
                os.replace(temporary, target)
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass
                restored.append(name)
            except OSError as error:
                try:
                    temporary.unlink()
                except OSError:
                    pass
                raise BackupError(f"could not restore {name}") from error
        return restored


def _command(args):
    backup = StateBackup(args.data_dir, args.backup_dir, args.keep)
    if args.command == "backup":
        snapshot = backup.create(args.label)
        if snapshot is None:
            print("no state files found; nothing to back up")
            return 2
        print(json.dumps({"snapshot": str(snapshot), "files": backup.verify(snapshot)["files"]},
                         ensure_ascii=False, indent=2))
        return 0
    if args.command == "list":
        print(json.dumps(backup.list(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "verify":
        manifest = backup.verify(args.snapshot)
        print(json.dumps({"ok": True, "files": manifest["files"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "restore":
        if not args.yes:
            print("refusing to restore without --yes", file=sys.stderr)
            return 2
        restored = backup.restore(args.snapshot, force=args.force)
        print(json.dumps({"restored": restored}, ensure_ascii=False, indent=2))
        return 0
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit backup/restore for bounded Yunlan state")
    parser.add_argument("--data-dir", default=None, help="native data directory")
    parser.add_argument("--backup-dir", default=None, help="backup directory (default: data-dir/backups)")
    parser.add_argument("--keep", type=int, default=MAX_SNAPSHOTS, help="number of snapshots to retain")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("backup", help="create one snapshot")
    one.add_argument("--label", default="manual")
    sub.add_parser("list", help="list snapshots")
    verify = sub.add_parser("verify", help="verify a snapshot")
    verify.add_argument("snapshot")
    restore = sub.add_parser("restore", help="restore a verified snapshot")
    restore.add_argument("snapshot")
    restore.add_argument("--yes", action="store_true", help="confirm the restore")
    restore.add_argument("--force", action="store_true", help="skip the before-restore safety snapshot (not recommended)")
    args = parser.parse_args(argv)
    return _command(args)


if __name__ == "__main__":
    raise SystemExit(main())

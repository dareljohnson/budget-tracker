"""Local backups and staged restores.

A snapshot is a ZIP of a consistent database copy (taken through SQLite's backup API),
the receipts folder, and a manifest. Restores are validated, extracted to a staging
folder, and applied before the next database connection, so the running app never
replaces a database it has open.
"""

from __future__ import annotations

import json
import ntpath
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from budget_tracker import __version__
from budget_tracker.config import data_dir, db_path

BACKUP_FORMAT = 1
AUTO_KEEP = 14
PRE_MIGRATION_KEEP = 3
REPLACED_KEEP = 2
DUE_AFTER_SECONDS = 24 * 60 * 60
DB_FILENAME = "budget_tracker.db"

BACKUP_LOCK = threading.Lock()


class BackupError(Exception):
    """A backup could not be created, validated, or applied."""


@dataclass(frozen=True, slots=True)
class BackupInfo:
    path: Path
    kind: str
    created_at: str
    size_bytes: int
    schema_version: int
    reason: str


# -- locations and status ------------------------------------------------


def backups_dir() -> Path:
    """Return the backup folder, creating it when needed."""
    path = data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def status_path() -> Path:
    return backups_dir() / "status.json"


def write_status(kind: str, ok: bool, message: str = "") -> None:
    """Record the outcome of the last backup or restore. Never raises."""
    try:
        payload = {
            "kind": kind,
            "ok": bool(ok),
            "at": datetime.now().isoformat(timespec="seconds"),
            "message": message,
        }
        target = status_path()
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    except Exception:  # noqa: BLE001 - status reporting must never break a backup
        pass


def read_status() -> dict | None:
    path = status_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# -- creation ------------------------------------------------------------


def create_backup(*, kind: str = "manual", reason: str = "") -> Path:
    """Take a snapshot of the database and receipts and return the ZIP path."""
    with BACKUP_LOCK:
        return _create_backup(kind=kind, reason=reason)


def _create_backup(*, kind: str, reason: str) -> Path:
    """Create a backup; the caller holds :data:`BACKUP_LOCK`."""
    folder = backups_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = _unique_path(folder / f"{kind}-{stamp}.zip")
    partial = target.with_name(target.name + ".tmp")
    work = Path(tempfile.mkdtemp(prefix=".work-", dir=folder))
    try:
        database = work / DB_FILENAME
        _copy_database(database)
        receipt_count = _copy_receipts(work / "receipts")
        manifest = {
            "format": BACKUP_FORMAT,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "reason": reason or kind,
            "kind": kind,
            "app_version": __version__,
            "schema_version": _schema_version(database),
            "transaction_count": _transaction_count(database),
            "receipt_count": receipt_count,
        }
        (work / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(work.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(work).as_posix())
        os.replace(partial, target)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a user-facing error
        _unlink(partial)
        _rmtree(work)
        write_status(kind, False, str(exc))
        if isinstance(exc, BackupError):
            raise
        raise BackupError(str(exc)) from exc
    finally:
        _rmtree(work)

    prune_backups()
    write_status(kind, True, str(target))
    return target


def create_backup_if_due() -> Path | None:
    """Create an automatic snapshot unless the newest one is under 24 hours old."""
    newest = None
    for path in backups_dir().glob("auto-*.zip"):
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        if newest is None or modified > newest:
            newest = modified
    if newest is not None and time.time() - newest < DUE_AFTER_SECONDS:
        return None
    return create_backup(kind="auto", reason="automatic")


def _copy_database(target: Path) -> None:
    source = db_path()
    if not source.exists():
        raise BackupError("There is no database to back up yet")
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            source_conn = sqlite3.connect(str(source))
            try:
                target_conn = sqlite3.connect(str(target))
                try:
                    source_conn.backup(target_conn)
                finally:
                    target_conn.close()
            finally:
                source_conn.close()
            return
        except sqlite3.OperationalError as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.2)
        except (sqlite3.Error, OSError) as exc:
            raise BackupError(f"The database could not be copied: {exc}") from exc
    raise BackupError(f"The database could not be copied: {last_error}")


def _copy_receipts(target: Path) -> int:
    source = data_dir() / "receipts"
    if not source.is_dir():
        return 0
    count = 0
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, destination)
        except FileNotFoundError:
            continue  # a receipt deleted mid-copy is skipped, not fatal
        count += 1
    return count


def _schema_version(database: Path) -> int:
    try:
        conn = sqlite3.connect(str(database))
        try:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return 0


def _transaction_count(database: Path) -> int:
    try:
        conn = sqlite3.connect(str(database))
        try:
            return int(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0])
        finally:
            conn.close()
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return 0


# -- listing and pruning -------------------------------------------------


def list_backups() -> list[BackupInfo]:
    """Return ZIP snapshots, newest first.

    A damaged ZIP falls back to its filename and file metadata instead of hiding it.
    """
    infos: list[BackupInfo] = []
    for path in backups_dir().glob("*.zip"):
        kind = _kind_from_name(path.name)
        try:
            stat = path.stat()
        except OSError:
            continue
        created_at = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        schema_version = 0
        reason = kind
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            if isinstance(manifest, dict):
                created_at = str(manifest.get("created_at") or created_at)
                schema_version = int(manifest.get("schema_version") or 0)
                reason = str(manifest.get("reason") or kind)
        except (OSError, KeyError, ValueError, TypeError, zipfile.BadZipFile):
            pass
        infos.append(
            BackupInfo(
                path=path,
                kind=kind,
                created_at=created_at,
                size_bytes=stat.st_size,
                schema_version=schema_version,
                reason=reason,
            )
        )
    infos.sort(key=lambda info: _mtime(info.path), reverse=True)
    return infos


def _kind_from_name(name: str) -> str:
    """Recover the snapshot kind from its filename (``pre-restore`` has a dash)."""
    for prefix in ("pre-restore", "pre-migration", "manual", "auto"):
        if name.startswith(prefix + "-"):
            return prefix
    return name.split("-", 1)[0] or "backup"


def prune_backups() -> None:
    """Keep 14 automatic snapshots, 3 pre-migration copies and 2 replaced folders."""
    folder = backups_dir()

    def prune_files(pattern: str, keep: int) -> None:
        paths = sorted(
            (path for path in folder.glob(pattern) if path.is_file()),
            key=_mtime,
            reverse=True,
        )
        for path in paths[keep:]:
            _unlink(path)

    prune_files("auto-*.zip", AUTO_KEEP)
    prune_files("pre-migration-*.db", PRE_MIGRATION_KEEP)

    replaced = sorted(
        (path for path in folder.glob("replaced-*") if path.is_dir()),
        key=_mtime,
        reverse=True,
    )
    for path in replaced[REPLACED_KEEP:]:
        _rmtree(path)


# -- restore validation and staging --------------------------------------


def stage_restore(zip_path: Path) -> Path:
    """Validate a backup ZIP and stage its contents for the next start."""
    with BACKUP_LOCK:
        return _stage_restore(Path(zip_path))


def _stage_restore(zip_path: Path) -> Path:
    data = data_dir()
    staging = data / "restore-staging"
    work = Path(tempfile.mkdtemp(prefix=".restore-", dir=data))
    try:
        try:
            archive = zipfile.ZipFile(zip_path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise BackupError(f"The backup file could not be opened: {exc}") from exc
        with archive:
            names = _validated_names(archive)
            if "manifest.json" not in names:
                raise BackupError("The backup is missing its manifest")
            if DB_FILENAME not in names:
                raise BackupError("The backup is missing its database")
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except (KeyError, ValueError, TypeError) as exc:
                raise BackupError("The backup manifest could not be read") from exc
            if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT:
                raise BackupError("This backup uses an unsupported format")
            declared = manifest.get("schema_version")
            if isinstance(declared, int) and declared > _latest_schema_version():
                raise BackupError(
                    f"The backup uses schema version {declared}, newer than this app supports"
                )
            for name in names:
                if name == "manifest.json" or name == DB_FILENAME or name.startswith("receipts/"):
                    _extract_member(archive, name, work)
        _validate_database(work / DB_FILENAME)
        if staging.exists():
            shutil.rmtree(staging)
        shutil.move(str(work), str(staging))
    except BackupError:
        _rmtree(work)
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        _rmtree(work)
        raise BackupError(f"The backup could not be staged: {exc}") from exc

    _write_marker(
        {
            "state": "staged",
            "step": "staged",
            "staging": str(staging),
            "zip": str(zip_path.resolve()),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    return staging


def _validated_names(archive: zipfile.ZipFile) -> list[str]:
    """Normalise member names and reject any that could escape the staging folder."""
    names: list[str] = []
    for info in archive.infolist():
        raw = info.filename
        name = raw.replace("\\", "/")
        if not name:
            continue
        if ntpath.splitdrive(name)[0] or name.startswith("/") or name.startswith("//"):
            raise BackupError(f"The backup contains an unsafe path: {raw}")
        if any(part == ".." for part in name.split("/")):
            raise BackupError(f"The backup contains an unsafe path: {raw}")
        names.append(name)
    return names


def _extract_member(archive: zipfile.ZipFile, name: str, destination: Path) -> None:
    if name.endswith("/"):
        return
    target = destination / name
    resolved = target.resolve()
    if not resolved.is_relative_to(destination.resolve()):
        raise BackupError(f"The backup contains an unsafe path: {name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with archive.open(name) as source, target.open("wb") as sink:
            shutil.copyfileobj(source, sink)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise BackupError(f"The backup member {name} could not be read: {exc}") from exc


def _latest_schema_version() -> int:
    from budget_tracker.db import MIGRATIONS

    return len(MIGRATIONS)


def _validate_database(database: Path) -> None:
    from budget_tracker.db import migrate

    if not database.is_file():
        raise BackupError("The backup is missing its database")
    try:
        conn = sqlite3.connect(str(database))
        try:
            check = conn.execute("PRAGMA integrity_check").fetchone()
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()
    except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
        raise BackupError(f"The backup database could not be read: {exc}") from exc
    if not check or check[0] != "ok":
        raise BackupError("The backup database failed its integrity check")
    if version > _latest_schema_version():
        raise BackupError(
            f"The backup uses schema version {version}, newer than this app supports"
        )

    dry_run = database.with_name(database.name + ".migrate-check.tmp")
    try:
        shutil.copy2(database, dry_run)
        conn = sqlite3.connect(str(dry_run))
        try:
            migrate(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - any migration failure refuses the backup
        raise BackupError(f"The backup database failed to migrate: {exc}") from exc
    finally:
        _unlink(dry_run)


# -- pending marker ------------------------------------------------------


def _marker_path() -> Path:
    return data_dir() / "restore-pending.json"


def pending_restore() -> dict | None:
    path = _marker_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_marker(marker: dict) -> None:
    path = _marker_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _clear_marker() -> None:
    _unlink(_marker_path())


# -- applying a staged restore -------------------------------------------


def apply_pending_restore() -> str | None:
    """Apply a staged restore before the next connection.

    Returns ``None`` when nothing is pending, a success message after applying, or the
    failure/warning message when the restore could not be applied. The current data is
    left usable on every failure path.
    """
    with BACKUP_LOCK:
        return _apply_pending_restore()


def _apply_pending_restore() -> str | None:
    marker = pending_restore()
    if marker is None:
        if _marker_path().exists():
            _clear_marker()
            message = "The restore marker could not be read; current data was kept."
            write_status("restore", False, message)
            return message
        return None

    state = marker.get("state")
    if state == "failed":
        message = str(marker.get("message") or "The last restore failed")
        _clear_marker()
        write_status("restore", False, message)
        return message

    staging = _staging_from_marker(marker)
    if staging is None:
        _clear_marker()
        message = "The staged restore is missing; current data was kept."
        write_status("restore", False, message)
        return message

    data = data_dir()
    live_database = Path(str(db_path()))
    live_receipts = data / "receipts"
    step = str(marker.get("step") or "moving-current")
    if state == "applying":
        replaced = _replaced_from_marker(marker)
        if step == "moving-current" and not marker.get("replaced"):
            replaced = None
    else:
        replaced = None

    try:
        if state != "applying":
            if live_database.exists():
                _create_backup(kind="pre-restore", reason="before restore")
            replaced = _new_replaced_dir()
            marker.update(
                state="applying",
                step="moving-current",
                staging=str(staging),
                replaced=str(replaced),
                created_at=marker.get("created_at")
                or datetime.now().isoformat(timespec="seconds"),
            )
            _write_marker(marker)
            step = "moving-current"

        if replaced is None:
            replaced = _new_replaced_dir()
            marker["replaced"] = str(replaced)

        if step == "moving-current":
            _move_current(replaced, live_database, live_receipts)
            marker.update(step="moved-current")
            _write_marker(marker)
            step = "moved-current"

        if step == "moved-current":
            _install_database(staging / DB_FILENAME, live_database)
            marker.update(step="installed-db")
            _write_marker(marker)
            step = "installed-db"

        if step == "installed-db":
            _install_receipts(staging / "receipts", live_receipts)
            marker.update(step="installed-receipts")
            _write_marker(marker)
        # installed-receipts (or a later unknown step) means the data is in place.

        _clear_marker()
        _rmtree(staging)
        message = "Restore applied; your restored data is in place."
        write_status("restore", True, message)
        return message
    except Exception as exc:  # noqa: BLE001 - keep the app usable on any failure
        message = f"Restore failed: {exc}"
        try:
            _rollback(replaced, live_database, live_receipts)
        except Exception:  # noqa: BLE001 - rollback is best effort
            pass
        try:
            marker.update(state="failed", step=step, message=message)
            _write_marker(marker)
        except Exception:  # noqa: BLE001 - a marker write must not mask the failure
            pass
        write_status("restore", False, message)
        return message


def _staging_from_marker(marker: dict) -> Path | None:
    raw = marker.get("staging")
    if not raw:
        return None
    try:
        staging = Path(str(raw))
        if not staging.is_dir():
            return None
        if not staging.resolve().is_relative_to(data_dir().resolve()):
            return None
    except (OSError, ValueError):
        return None
    return staging


def _replaced_from_marker(marker: dict) -> Path | None:
    raw = marker.get("replaced")
    if raw:
        try:
            candidate = Path(str(raw))
            if candidate.is_dir() and candidate.resolve().is_relative_to(backups_dir().resolve()):
                return candidate
        except (OSError, ValueError):
            pass
    candidates = sorted(
        (path for path in backups_dir().glob("replaced-*") if path.is_dir()),
        key=_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _new_replaced_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return _unique_path(backups_dir() / f"replaced-{stamp}")


def _move_current(replaced: Path, live_database: Path, live_receipts: Path) -> None:
    replaced.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(live_database) + suffix)
        if source.exists():
            os.replace(source, replaced / source.name)
    _merge_move(live_receipts, replaced / "receipts")


def _install_database(staged: Path, live: Path) -> None:
    if not staged.exists():
        if live.exists():
            return  # already installed during an interrupted earlier attempt
        raise BackupError("The staged database is missing")
    for suffix in ("-wal", "-shm"):
        stale = Path(str(live) + suffix)
        _unlink(stale)
    os.replace(staged, live)


def _install_receipts(staged: Path, live: Path) -> None:
    if not staged.exists():
        if live.exists():
            return  # already installed during an interrupted earlier attempt
        live.mkdir(parents=True, exist_ok=True)
        return
    if live.exists():
        shutil.rmtree(live)
    shutil.move(str(staged), str(live))


def _rollback(replaced: Path | None, live_database: Path, live_receipts: Path) -> None:
    if replaced is None or not replaced.is_dir():
        return
    for suffix in ("", "-wal", "-shm"):
        _unlink(Path(str(live_database) + suffix))
    for suffix in ("", "-wal", "-shm"):
        source = replaced / (live_database.name + suffix)
        if source.exists():
            try:
                os.replace(source, Path(str(live_database) + suffix))
            except OSError:
                pass
    old_receipts = replaced / "receipts"
    if old_receipts.exists():
        _rmtree(live_receipts)
        try:
            shutil.move(str(old_receipts), str(live_receipts))
        except OSError:
            pass


# -- small filesystem helpers -------------------------------------------


def _merge_move(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    if not destination.exists():
        shutil.move(str(source), str(destination))
        return
    for item in list(source.iterdir()):
        target = destination / item.name
        if item.is_dir():
            _merge_move(item, target)
        else:
            os.replace(item, target)
    try:
        source.rmdir()
    except OSError:
        pass


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _rmtree(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass

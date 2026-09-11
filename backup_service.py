"""Admin-only SQLite backup/restore helpers. Never exposed directly via static routes."""
from __future__ import annotations
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def list_backups():
    return sorted(BACKUP_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)


def create_backup(db_path: str | Path) -> Path:
    """Create timestamped backup. Never overwrites existing file (timestamp is unique;
    if collision, append counter). Returns backup path."""
    src = Path(db_path)
    base = f"adufarms_{timestamp()}.db"
    dst = BACKUP_DIR / base
    counter = 1
    while dst.exists():
        dst = BACKUP_DIR / f"adufarms_{timestamp()}_{counter}.db"
        counter += 1
    # Use SQLite backup API for a consistent snapshot, fallback to copy.
    try:
        src_conn = sqlite3.connect(str(src))
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
    except Exception:
        if dst.exists():
            dst.unlink()
        shutil.copy2(src, dst)
    return dst


def safe_restore(db_path: str | Path, backup_name: str) -> Path:
    """Restore from a backup inside BACKUP_DIR only (prevents path traversal).
    Creates a pre-restore safety backup first. Returns pre-restore backup path."""
    safe = Path(backup_name).name
    src = BACKUP_DIR / safe
    if not src.exists() or src.suffix != ".db":
        raise ValueError("Selected backup is unavailable.")
    if src.resolve().parent != BACKUP_DIR.resolve():
        raise ValueError("Invalid backup selection.")
    dst = Path(db_path)
    # Safety backup before restore (never silently overwrite the only backup)
    pre = BACKUP_DIR / f"adufarms_pre_restore_{timestamp()}.db"
    shutil.copy2(dst, pre)
    shutil.copy2(src, dst)
    return pre
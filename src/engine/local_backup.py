"""单机 data/ 顶层文件的可验证备份与无覆盖恢复。运行前先停服务。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone


MANIFEST = "backup_manifest.json"


def inspect_source(source_root: str | Path) -> dict:
    """只读检查现有备份范围；不打开或修改用户文件内容。"""
    source = Path(source_root).resolve(strict=True)
    if not source.is_dir():
        raise ValueError("备份源必须是目录")
    included, excluded = [], []
    for path in sorted(source.iterdir()):
        if path.is_symlink():
            raise ValueError(f"备份不接受符号链接：{path.name}")
        if (path.is_file() and not path.name.startswith(".")
                and path.name != MANIFEST
                and not path.name.endswith(("-wal", "-shm", ".tmp"))):
            included.append(path.name)
        else:
            excluded.append(path.name)
    return {"included": included, "excluded": excluded,
            "complete_for_source": not excluded}


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _new_staging(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=".hsk-backup-", dir=parent))


def create_backup(source_root: str | Path, target: str | Path) -> dict:
    """备份 data/ 顶层常规文件；coach.db 使用 SQLite backup API。"""
    source = Path(source_root).resolve(strict=True)
    destination = Path(target).resolve(strict=False)
    if not source.is_dir() or destination.exists() or destination.is_relative_to(source):
        raise ValueError("备份源必须是目录，目标必须是源目录外不存在的新路径")
    scope = inspect_source(source)
    staging = _new_staging(destination.parent)
    try:
        entries = []
        for path in sorted(source.iterdir()):
            if path.is_symlink():
                raise ValueError(f"备份不接受符号链接：{path.name}")
            if (not path.is_file() or path.name.startswith(".") or path.name == MANIFEST
                    or path.name.endswith(("-wal", "-shm", ".tmp"))):
                continue
            output = staging / path.name
            if path.name == "coach.db":
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
                    with closing(sqlite3.connect(output)) as snapshot:
                        conn.backup(snapshot)
            else:
                shutil.copy2(path, output)
            entries.append({"name": path.name, "size": output.stat().st_size,
                            "sha256": _digest(output)})
        manifest = {"version": 1,
                    "created_utc": datetime.now(timezone.utc).isoformat(),
                    "scope": "data top-level files only; .env and subdirectories excluded",
                    "excluded_paths": scope["excluded"],
                    "complete_for_source": scope["complete_for_source"],
                    "files": entries}
        (staging / MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(staging, destination)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def verify_backup(bundle: str | Path) -> dict:
    """验证清单及所有文件哈希；路径穿越和缺失/额外文件均拒绝。"""
    root = Path(bundle).resolve(strict=True)
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    entries = manifest.get("files")
    if manifest.get("version") != 1 or not isinstance(entries, list):
        raise ValueError("备份清单版本或格式无效")
    names = set()
    for item in entries:
        name = item.get("name") if isinstance(item, dict) else None
        if (not isinstance(name, str) or name in {"", ".", "..", MANIFEST}
                or Path(name).name != name or "/" in name or "\\" in name
                or name in names):
            raise ValueError("备份清单包含无效文件名")
        names.add(name)
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"备份文件缺失或无效：{name}")
        if path.stat().st_size != item.get("size") or _digest(path) != item.get("sha256"):
            raise ValueError(f"备份文件校验失败：{name}")
    if {path.name for path in root.iterdir()} != names | {MANIFEST}:
        raise ValueError("备份目录含清单之外的文件")
    if "coach.db" in names:
        try:
            with closing(sqlite3.connect((root / "coach.db").as_uri() + "?mode=ro", uri=True)) as conn:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise ValueError("SQLite 备份完整性检查失败")
        except sqlite3.DatabaseError as exc:
            raise ValueError("SQLite 备份完整性检查失败") from exc
    return manifest


def restore_backup(bundle: str | Path, target: str | Path) -> dict:
    """只恢复到不存在的新目录，绝不覆盖现有 data/。"""
    source = Path(bundle).resolve(strict=True)
    destination = Path(target).resolve(strict=False)
    if destination.exists() or destination.is_relative_to(source):
        raise ValueError("恢复目标必须是备份目录外不存在的新路径")
    manifest = verify_backup(source)
    staging = _new_staging(destination.parent)
    try:
        for item in manifest["files"]:
            shutil.copy2(source / item["name"], staging / item["name"])
        shutil.copy2(source / MANIFEST, staging / MANIFEST)
        verify_backup(staging)
        (staging / MANIFEST).unlink()
        os.replace(staging, destination)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> None:
    parser = argparse.ArgumentParser(description="HSK-AI-Coach 单机数据备份/校验/无覆盖恢复")
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create")
    create.add_argument("source")
    create.add_argument("target")
    verify = sub.add_parser("verify")
    verify.add_argument("bundle")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("source")
    restore = sub.add_parser("restore")
    restore.add_argument("bundle")
    restore.add_argument("target")
    args = parser.parse_args()
    if args.action == "create":
        result = create_backup(args.source, args.target)
    elif args.action == "verify":
        result = verify_backup(args.bundle)
    elif args.action == "inspect":
        result = inspect_source(args.source)
        print(f"inspect: included {len(result['included'])}; excluded "
              f"{len(result['excluded'])}; complete_for_source="
              f"{result['complete_for_source']}")
        return
    else:
        result = restore_backup(args.bundle, args.target)
    excluded = (str(len(result["excluded_paths"]))
                if "excluded_paths" in result else "unknown (legacy manifest)")
    print(f"{args.action}: verified {len(result['files'])} files; "
          f"excluded {excluded}; secrets not printed")


if __name__ == "__main__":
    main()

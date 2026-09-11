#!/usr/bin/env python3
"""将历史 AMAC/CSRC 根目录数据迁移到 data/ 布局。

幂等：目标已存在且非空时跳过搬迁，只补齐缺失目录与 config 路径。
冲突：源与目标都非空且内容不同 → 退出码 1，不覆盖。

用法::

    python scripts/migrate_layout.py --dry-run
    python scripts/migrate_layout.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (旧相对路径, 新相对路径)
MOVES: tuple[tuple[str, str], ...] = (
    ("AMAC/cases", "data/amac/cases"),
    ("AMAC/summaries", "data/amac/summaries"),
    ("AMAC/reports", "data/amac/reports"),
    ("CSRC/cases", "data/csrc/cases"),
    ("CSRC/summaries", "data/csrc/summaries"),
    ("CSRC/reports", "data/csrc/reports"),
)

# config.json data_roots 中旧默认值 → 新默认值
CONFIG_REWRITES: dict[str, str] = {
    "AMAC/cases": "data/amac/cases",
    "AMAC/summaries": "data/amac/summaries",
    "AMAC/reports": "data/amac/reports",
    "CSRC/cases": "data/csrc/cases",
    "CSRC/summaries": "data/csrc/summaries",
    "CSRC/reports": "data/csrc/reports",
    "AMAC\\cases": "data/amac/cases",
    "AMAC\\summaries": "data/amac/summaries",
    "AMAC\\reports": "data/amac/reports",
    "CSRC\\cases": "data/csrc/cases",
    "CSRC\\summaries": "data/csrc/summaries",
    "CSRC\\reports": "data/csrc/reports",
}


def _dir_has_entries(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def _normalize_rel(value: str) -> str:
    return value.replace("\\", "/").strip().strip("/")


def migrate_data(dry_run: bool) -> list[str]:
    notes: list[str] = []
    for src_rel, dst_rel in MOVES:
        src = ROOT / src_rel
        dst = ROOT / dst_rel
        if not src.exists():
            notes.append(f"skip  {src_rel}（源不存在）")
            if not dst.exists() and not dry_run:
                dst.mkdir(parents=True, exist_ok=True)
                notes.append(f"mkdir {dst_rel}")
            continue
        if not _dir_has_entries(src):
            notes.append(f"skip  {src_rel}（源为空）")
            if not dry_run:
                dst.mkdir(parents=True, exist_ok=True)
            continue
        if _dir_has_entries(dst):
            notes.append(f"CONFLICT {src_rel} → {dst_rel}：目标已存在且非空，请人工合并")
            raise SystemExit(1)
        if dry_run:
            notes.append(f"move  {src_rel} → {dst_rel}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        notes.append(f"moved {src_rel} → {dst_rel}")
    return notes


def rewrite_config(dry_run: bool) -> list[str]:
    notes: list[str] = []
    path = ROOT / "config.json"
    if not path.exists():
        notes.append("skip  config.json（不存在）")
        return notes
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        notes.append(f"error config.json 无法解析：{exc}")
        return notes
    roots = data.get("data_roots")
    if not isinstance(roots, dict):
        notes.append("skip  config.json 无 data_roots")
        return notes
    changed = False
    for key, value in list(roots.items()):
        if not isinstance(value, str):
            continue
        new = CONFIG_REWRITES.get(value) or CONFIG_REWRITES.get(_normalize_rel(value))
        if new and new != value:
            notes.append(f"config data_roots.{key}: {value} → {new}")
            roots[key] = new
            changed = True
    if changed and not dry_run:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        notes.append("wrote config.json")
    elif changed:
        notes.append("dry-run: 不写 config.json")
    else:
        notes.append("skip  config.json data_roots 无需改写")
    return notes


def ensure_skeleton(dry_run: bool) -> list[str]:
    notes: list[str] = []
    for _, dst_rel in MOVES:
        dst = ROOT / dst_rel
        if dst.exists():
            continue
        if dry_run:
            notes.append(f"mkdir {dst_rel}")
        else:
            dst.mkdir(parents=True, exist_ok=True)
            notes.append(f"mkdir {dst_rel}")
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移 AMAC/CSRC 根目录数据到 data/")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不改动磁盘")
    args = parser.parse_args()

    print(f"项目根：{ROOT}")
    print(f"模式：{'dry-run' if args.dry_run else 'apply'}")
    for note in migrate_data(args.dry_run) + ensure_skeleton(args.dry_run) + rewrite_config(args.dry_run):
        print(" ", note)
    print("完成。AMAC_Discipline_PDFs/ 未改动（请自行外迁）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

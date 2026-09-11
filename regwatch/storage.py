"""数据访问层：统一读写案例、摘要与索引。

磁盘布局**保持与原脚本完全一致**，不做数据迁移：

AMAC::

    AMAC/cases/
        _index.json                 抓取索引
        _org_type_cache.json        机构类型缓存
        _org_type_manual.json       人工补全清单
        institution/{case_id}.json  scfjg（机构）
        personnel/{case_id}.json    scfry（人员）
    AMAC/summaries/
        _summary_index.json
        {case_id}_summary.json      扁平结构

CSRC::

    CSRC/cases/
        _index.json
        {Bureau}/{measure|penalty}/{case_id}.json
    CSRC/summaries/
        _summary_index.json
        {Bureau}/{case_type}/{case_id}_summary.json

对外提供两层能力：

- **写入层**：``AmacIndex`` / ``CsrcIndex`` / ``SummaryIndex`` / ``OrgTypeCache``
  与 ``write_json``，供采集与摘要流程持久化状态。
- **读取层**：``CaseRow`` + ``build_catalog``，把两套异构数据归一成统一的
  案例视图（不含 ``raw_text``），供统计与网页端使用；正文按需通过
  ``read_case`` 单独读取。
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .config import Config, get_config
from .logutil import get_logger

logger = get_logger("storage")

__all__ = [
    "DATASET_AMAC",
    "DATASET_CSRC",
    "DATASETS",
    "DATASET_LABELS",
    "CSRC_CASE_TYPES",
    "CSRC_CASE_TYPE_LABELS",
    "MAX_CATALOG_FILE_BYTES",
    "read_json",
    "write_json",
    "amac_cases_dir",
    "amac_summaries_dir",
    "amac_reports_dir",
    "csrc_cases_dir",
    "csrc_summaries_dir",
    "AmacIndex",
    "CsrcIndex",
    "SummaryIndex",
    "OrgTypeCache",
    "CaseRow",
    "DatasetOverview",
    "build_catalog",
    "invalidate_catalog",
    "filter_rows",
    "read_case",
    "resolve_case_path",
    "iter_summary_files",
    "dataset_overview",
]

# ──────────────────────────── 常量 ────────────────────────────

DATASET_AMAC = "amac"
DATASET_CSRC = "csrc"
DATASETS: Tuple[str, ...] = (DATASET_AMAC, DATASET_CSRC)

DATASET_LABELS: Dict[str, str] = {
    DATASET_AMAC: "中基协（AMAC）",
    DATASET_CSRC: "证监会（CSRC）",
}

AMAC_CATEGORY_DIRS: Dict[str, str] = {"scfjg": "institution", "scfry": "personnel"}
AMAC_DIR_CATEGORIES: Dict[str, str] = {v: k for k, v in AMAC_CATEGORY_DIRS.items()}

CSRC_CASE_TYPES: Tuple[str, ...] = ("penalty", "measure")
CSRC_CASE_TYPE_LABELS: Dict[str, str] = {"penalty": "行政处罚", "measure": "监管措施"}

# 跳过超过该体积的 JSON（防止误把超大导出文件当成案例读入）
MAX_CATALOG_FILE_BYTES = 20 * 1024 * 1024


# ──────────────────────────── JSON 读写 ────────────────────────────


def read_json(path: Path | str) -> Optional[Dict[str, Any]]:
    """读取 JSON 文件；不存在或损坏时返回 ``None``。"""
    path = Path(path)
    try:
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("读取 JSON 失败 %s: %s", path.name, exc)
        return None
    return data if isinstance(data, dict) else None


def write_json(path: Path | str, data: Dict[str, Any], indent: int = 2) -> Path:
    """原子写入 JSON 文件（先写临时文件再替换，避免半截文件）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)
    return path


# ──────────────────────────── 路径解析 ────────────────────────────


def amac_cases_dir(config: Optional[Config] = None) -> Path:
    return (config or get_config()).data_root("amac_cases")


def amac_summaries_dir(config: Optional[Config] = None) -> Path:
    return (config or get_config()).data_root("amac_summaries")


def amac_reports_dir(config: Optional[Config] = None) -> Path:
    return (config or get_config()).data_root("amac_reports")


def csrc_cases_dir(config: Optional[Config] = None) -> Path:
    return (config or get_config()).data_root("csrc_cases")


def csrc_summaries_dir(config: Optional[Config] = None) -> Path:
    return (config or get_config()).data_root("csrc_summaries")


def amac_case_path(
    case_id: str,
    category: str = "",
    config: Optional[Config] = None,
) -> Optional[Path]:
    """定位 AMAC 案例原文；找不到返回 ``None``。"""
    root = amac_cases_dir(config)
    candidates: List[str] = []
    if category in AMAC_CATEGORY_DIRS:
        candidates.append(AMAC_CATEGORY_DIRS[category])
    else:
        candidates.extend(AMAC_CATEGORY_DIRS.values())
    for sub in candidates:
        path = root / sub / f"{case_id}.json"
        if path.exists():
            return path
    return None


def csrc_case_path(
    case_id: str,
    bureau: str = "",
    case_type: str = "",
    config: Optional[Config] = None,
) -> Optional[Path]:
    """定位 CSRC 案例原文；找不到返回 ``None``。"""
    root = csrc_cases_dir(config)
    if bureau and case_type:
        path = root / bureau / case_type / f"{case_id}.json"
        if path.exists():
            return path
    if case_id and root.exists():
        # 兜底：在 {Bureau}/{case_type} 两层目录中按文件名搜索
        for found in root.glob(f"*/*/{case_id}.json"):
            if found.exists():
                return found
    return None


def resolve_case_path(
    dataset: str,
    case_id: str,
    bureau: str = "",
    case_type: str = "",
    category: str = "",
    config: Optional[Config] = None,
) -> Optional[Path]:
    """按数据集定位案例原文路径。"""
    if dataset == DATASET_AMAC:
        return amac_case_path(case_id, category, config)
    if dataset == DATASET_CSRC:
        return csrc_case_path(case_id, bureau, case_type, config)
    return None


def iter_summary_files(
    dataset: str,
    config: Optional[Config] = None,
) -> Iterator[Path]:
    """遍历某数据集下全部摘要文件（跳过下划线开头的索引文件）。"""
    root = amac_summaries_dir(config) if dataset == DATASET_AMAC else csrc_summaries_dir(config)
    if not root.exists():
        return
    for path in sorted(root.rglob("*_summary.json")):
        if path.name.startswith("_"):
            continue
        try:
            if path.stat().st_size > MAX_CATALOG_FILE_BYTES:
                logger.warning("跳过异常大的摘要文件：%s", path.name)
                continue
        except OSError:
            continue
        yield path


# ──────────────────────────── 抓取索引 ────────────────────────────


class AmacIndex:
    """AMAC 抓取索引（``{cases_dir}/_index.json``）。

    结构::

        {
          "last_updated": "...",
          "categories": {
            "Institution": {"last_crawled_page": 0, "links": [
                {"link_url": ..., "title": ..., "date": ..., "status": "pending|done|failed"}
            ]},
            "Personnel": {...}
          }
        }
    """

    FILENAME = "_index.json"
    CATEGORIES: Tuple[str, ...] = ("Institution", "Personnel")
    STATUSES: Tuple[str, ...] = ("total", "done", "pending", "failed")

    def __init__(self, cases_dir: Path) -> None:
        self.cases_dir = Path(cases_dir)
        self.path = self.cases_dir / self.FILENAME
        self._lock = threading.RLock()
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        data = read_json(self.path)
        if not data:
            return {"last_updated": "", "categories": {}}
        data.setdefault("categories", {})
        return data

    def reload(self) -> "AmacIndex":
        with self._lock:
            self.data = self._load()
        return self

    def save(self) -> None:
        with self._lock:
            self.data["last_updated"] = datetime.now().isoformat(timespec="seconds")
            write_json(self.path, self.data)

    # ── 查询 ──

    def get_category_links(self, category: str) -> List[Dict[str, Any]]:
        with self._lock:
            cat = self.data.get("categories", {}).get(category, {})
            return [dict(item) for item in cat.get("links", [])]

    def get_last_crawled_page(self, category: str) -> int:
        with self._lock:
            cat = self.data.get("categories", {}).get(category, {})
            return int(cat.get("last_crawled_page", -1))

    def get_pending_links(self, category: str) -> List[Dict[str, Any]]:
        return [item for item in self.get_category_links(category) if item.get("status") == "pending"]

    def get_failed_links(self, category: str) -> List[Dict[str, Any]]:
        return [item for item in self.get_category_links(category) if item.get("status") == "failed"]

    def get_stats(self, category: str) -> Dict[str, int]:
        with self._lock:
            links = self.get_category_links(category)
        stats = {key: 0 for key in self.STATUSES}
        stats["total"] = len(links)
        for item in links:
            status = item.get("status", "pending")
            if status in stats:
                stats[status] += 1
        return stats

    # ── 写入 ──

    def update_category(self, category: str, links: Sequence[Dict[str, Any]], last_page: int) -> None:
        """合并新发现的链接；已存在条目只刷新标题与日期，不重置状态。"""
        with self._lock:
            categories = self.data.setdefault("categories", {})
            existing = categories.get(category, {})
            indexed: Dict[str, Dict[str, Any]] = {
                item["link_url"]: item for item in existing.get("links", [])
            }
            for link in links:
                url = link["link_url"]
                date_value = link.get("date", "")
                date_str = date_value.isoformat() if hasattr(date_value, "isoformat") else str(date_value)
                if url in indexed:
                    indexed[url]["date"] = date_str
                    indexed[url]["title"] = link.get("title", "")
                else:
                    indexed[url] = {
                        "link_url": url,
                        "title": link.get("title", ""),
                        "date": date_str,
                        "status": "pending",
                    }
            categories[category] = {
                "last_crawled_page": last_page,
                "links": list(indexed.values()),
            }
        self.save()

    def _set_status(self, category: str, link_url: str, status: str, **extra: Any) -> None:
        with self._lock:
            categories = self.data.setdefault("categories", {})
            links = categories.get(category, {}).get("links", [])
            for item in links:
                if item.get("link_url") == link_url:
                    item["status"] = status
                    item.update(extra)
                    break
        self.save()

    def mark_done(self, category: str, link_url: str) -> None:
        self._set_status(category, link_url, "done")

    def mark_failed(self, category: str, link_url: str, error: str = "") -> None:
        self._set_status(category, link_url, "failed", error=error)


class CsrcIndex:
    """CSRC 抓取索引（``{cases_dir}/_index.json``），按 ``{Bureau}/{case_type}`` 组织。"""

    FILENAME = "_index.json"
    STATUSES: Tuple[str, ...] = ("total", "done", "pending", "failed", "skipped_not_fund")

    def __init__(self, cases_dir: Path) -> None:
        self.cases_dir = Path(cases_dir)
        self.path = self.cases_dir / self.FILENAME
        self._lock = threading.RLock()
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        data = read_json(self.path)
        if not data:
            return {"last_updated": "", "sources": {}}
        data.setdefault("sources", {})
        return data

    def reload(self) -> "CsrcIndex":
        with self._lock:
            self.data = self._load()
        return self

    def save(self) -> None:
        with self._lock:
            self.data["last_updated"] = datetime.now().isoformat(timespec="seconds")
            write_json(self.path, self.data)

    @staticmethod
    def source_key(bureau: str, case_type: str) -> str:
        return f"{bureau}/{case_type}"

    def source_keys(self) -> List[str]:
        with self._lock:
            return sorted(self.data.get("sources", {}).keys())

    # ── 查询 ──

    def get_source_links(self, source_key: str) -> List[Dict[str, Any]]:
        with self._lock:
            source = self.data.get("sources", {}).get(source_key, {})
            return [dict(item) for item in source.get("links", [])]

    def get_last_crawled_page(self, source_key: str) -> int:
        with self._lock:
            source = self.data.get("sources", {}).get(source_key, {})
            return int(source.get("last_crawled_page", 0))

    def get_pending_links(self, source_key: str) -> List[Dict[str, Any]]:
        return [item for item in self.get_source_links(source_key) if item.get("status") == "pending"]

    def get_failed_links(self, source_key: str) -> List[Dict[str, Any]]:
        return [item for item in self.get_source_links(source_key) if item.get("status") == "failed"]

    def get_stats(self, source_key: str) -> Dict[str, int]:
        with self._lock:
            links = self.get_source_links(source_key)
        stats = {key: 0 for key in self.STATUSES}
        stats["total"] = len(links)
        for item in links:
            status = item.get("status", "pending")
            if status in stats:
                stats[status] += 1
        return stats

    def total_stats(self) -> Dict[str, int]:
        """汇总全部来源的索引状态。"""
        total = {key: 0 for key in self.STATUSES}
        for source_key in self.source_keys():
            for key, value in self.get_stats(source_key).items():
                total[key] += value
        return total

    def link_lookup_by_content_id(self) -> Dict[str, Dict[str, Any]]:
        """建立 ``内容编号 → 索引条目`` 的映射，用于补全被跳过案例的标题与日期。"""
        lookup: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            sources = dict(self.data.get("sources", {}))
        for source_key, source in sources.items():
            bureau, _, case_type = source_key.partition("/")
            for item in source.get("links", []):
                url = str(item.get("link_url", ""))
                match = re.search(r"/(c\d+)/content\.shtml", url) or re.search(r"(c\d{6,})", url)
                if not match:
                    continue
                lookup[match.group(1)] = {
                    "title": item.get("title", ""),
                    "date": item.get("date", ""),
                    "bureau": bureau,
                    "case_type": case_type,
                    "link_url": url,
                }
        return lookup

    # ── 写入 ──

    def update_source(self, source_key: str, links: Sequence[Dict[str, Any]], last_page: int) -> None:
        with self._lock:
            sources = self.data.setdefault("sources", {})
            existing = sources.get(source_key, {})
            indexed: Dict[str, Dict[str, Any]] = {
                item["link_url"]: item for item in existing.get("links", [])
            }
            for link in links:
                url = link["link_url"]
                date_value = link.get("date", "")
                date_str = date_value.isoformat() if hasattr(date_value, "isoformat") else str(date_value)
                if url in indexed:
                    indexed[url]["date"] = date_str
                    indexed[url]["title"] = link.get("title", "")
                else:
                    indexed[url] = {
                        "link_url": url,
                        "title": link.get("title", ""),
                        "date": date_str,
                        "status": "pending",
                    }
            sources[source_key] = {
                "last_crawled_page": last_page,
                "links": list(indexed.values()),
            }
        self.save()

    def _set_status(self, source_key: str, link_url: str, status: str, **extra: Any) -> None:
        with self._lock:
            sources = self.data.setdefault("sources", {})
            links = sources.get(source_key, {}).get("links", [])
            for item in links:
                if item.get("link_url") == link_url:
                    item["status"] = status
                    item.update(extra)
                    break
        self.save()

    def mark_done(self, source_key: str, link_url: str) -> None:
        self._set_status(source_key, link_url, "done")

    def mark_failed(self, source_key: str, link_url: str, error: str = "") -> None:
        self._set_status(source_key, link_url, "failed", error=error)

    def mark_skipped(self, source_key: str, link_url: str, reason: str = "not_fund") -> None:
        self._set_status(source_key, link_url, "skipped_not_fund", skip_reason=reason)


class SummaryIndex:
    """结构化摘要索引（``{summaries_dir}/_summary_index.json``）。

    AMAC 与 CSRC 共用同一结构；差异仅在状态取值：
    CSRC 会额外写入 ``skipped``（LLM 判定非基金相关，无摘要文件）。
    """

    FILENAME = "_summary_index.json"

    def __init__(self, summaries_dir: Path) -> None:
        self.summaries_dir = Path(summaries_dir)
        self.path = self.summaries_dir / self.FILENAME
        self._lock = threading.RLock()
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        data = read_json(self.path)
        if not data:
            return {"last_updated": "", "cases": {}}
        data.setdefault("cases", {})
        return data

    def reload(self) -> "SummaryIndex":
        with self._lock:
            self.data = self._load()
        return self

    def save(self) -> None:
        with self._lock:
            self.data["last_updated"] = datetime.now().isoformat(timespec="seconds")
            write_json(self.path, self.data)

    # ── 查询 ──

    def info(self, case_id: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self.data.get("cases", {}).get(case_id, {}))

    def status_of(self, case_id: str) -> str:
        return str(self.info(case_id).get("status", "pending"))

    def is_done(self, case_id: str) -> bool:
        """``done`` 与 ``skipped`` 都视为已处理完毕，不再重复调用模型。"""
        return self.status_of(case_id) in ("done", "skipped")

    def get_pending_cases(self, all_case_ids: Sequence[str]) -> List[str]:
        return [cid for cid in all_case_ids if not self.is_done(cid)]

    def get_failed_cases(self) -> List[str]:
        with self._lock:
            return [
                cid for cid, info in self.data.get("cases", {}).items()
                if info.get("status") == "failed"
            ]

    def get_skipped_cases(self) -> List[str]:
        with self._lock:
            return [
                cid for cid, info in self.data.get("cases", {}).items()
                if info.get("status") == "skipped"
            ]

    def get_stats(self, total_count: int = 0) -> Dict[str, int]:
        with self._lock:
            cases = dict(self.data.get("cases", {}))
        stats = {"done": 0, "skipped": 0, "failed": 0}
        for info in cases.values():
            status = info.get("status")
            if status in stats:
                stats[status] += 1
        handled = stats["done"] + stats["skipped"] + stats["failed"]
        stats["total"] = max(total_count, len(cases))
        stats["pending"] = max(0, stats["total"] - handled)
        return stats

    # ── 写入 ──

    def mark_done(self, case_id: str, summary_file: str) -> None:
        with self._lock:
            self.data.setdefault("cases", {})[case_id] = {
                "status": "done",
                "summary_file": summary_file,
                "extract_time": datetime.now().isoformat(timespec="seconds"),
            }
        self.save()

    def mark_skipped(self, case_id: str, reason: str) -> None:
        with self._lock:
            self.data.setdefault("cases", {})[case_id] = {
                "status": "skipped",
                "reason": reason,
                "extract_time": datetime.now().isoformat(timespec="seconds"),
            }
        self.save()

    def mark_failed(self, case_id: str, error: str) -> None:
        with self._lock:
            cases = self.data.setdefault("cases", {})
            info = cases.get(case_id, {})
            info["status"] = "failed"
            info["error"] = error
            cases[case_id] = info
        self.save()

    def forget(self, case_id: str) -> None:
        """从索引中移除某案例（用于强制重新提取）。"""
        with self._lock:
            self.data.setdefault("cases", {}).pop(case_id, None)
        self.save()


class OrgTypeCache:
    """机构登记类型本地缓存（``{cases_dir}/_org_type_cache.json``）。"""

    FILENAME = "_org_type_cache.json"

    def __init__(self, cases_dir: Path) -> None:
        self.cases_dir = Path(cases_dir)
        self.path = self.cases_dir / self.FILENAME
        self._lock = threading.RLock()
        self.data: Dict[str, str] = self._load()

    def _load(self) -> Dict[str, str]:
        data = read_json(self.path)
        if not data:
            return {}
        return {str(k): str(v) for k, v in data.items() if isinstance(k, str)}

    def reload(self) -> "OrgTypeCache":
        with self._lock:
            self.data = self._load()
        return self

    def save(self) -> None:
        with self._lock:
            write_json(self.path, dict(self.data))

    def get(self, org_name: str) -> Optional[str]:
        with self._lock:
            return self.data.get(org_name)

    def set(self, org_name: str, org_type: str) -> None:
        if not org_name or not org_type:
            return
        with self._lock:
            self.data[org_name] = org_type
        self.save()

    def all(self) -> Dict[str, str]:
        with self._lock:
            return dict(self.data)

    def __len__(self) -> int:
        with self._lock:
            return len(self.data)


# ──────────────────────────── 统一案例视图 ────────────────────────────


@dataclass
class CaseRow:
    """归一化后的案例视图（不含 ``raw_text``，正文按需单独读取）。"""

    dataset: str
    case_id: str
    date: str = ""
    title: str = ""
    bureau: str = ""
    case_type: str = ""
    category: str = ""
    entity: str = ""
    entity_type: str = ""
    org_type: str = ""
    violation_type: str = ""
    punishment: str = ""
    involved_fund: str = ""
    violation_summary: str = ""
    legal_basis: str = ""
    penalty_amount: str = ""
    market_ban: str = ""
    source_url: str = ""
    summary_file: str = ""
    case_file: str = ""
    status: str = "done"           # done / failed / skipped
    error: str = ""
    note: str = ""                 # 例如非基金相关的判定理由

    # ── 展示辅助 ──

    @property
    def dataset_label(self) -> str:
        return DATASET_LABELS.get(self.dataset, self.dataset)

    @property
    def case_type_label(self) -> str:
        if self.dataset == DATASET_CSRC:
            return CSRC_CASE_TYPE_LABELS.get(self.case_type, self.case_type or "—")
        return "纪律处分"

    @property
    def category_label(self) -> str:
        if self.dataset == DATASET_AMAC:
            return {"scfjg": "机构", "scfry": "人员"}.get(self.category, self.category or "—")
        return self.case_type_label

    @property
    def entity_display(self) -> str:
        return self.entity or self.title or self.case_id

    @property
    def year(self) -> str:
        return self.date[:4] if len(self.date) >= 4 else ""

    @property
    def month(self) -> str:
        return self.date[:7] if len(self.date) >= 7 else ""

    @property
    def violation_types(self) -> List[str]:
        return split_multi_value(self.violation_type)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.update(
            dataset_label=self.dataset_label,
            case_type_label=self.case_type_label,
            category_label=self.category_label,
            entity_display=self.entity_display,
            year=self.year,
            month=self.month,
            violation_types=self.violation_types,
        )
        return data


@dataclass
class DatasetOverview:
    """数据集层面的概览指标。"""

    dataset: str
    label: str = ""
    case_files: int = 0
    summary_files: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    pending: int = 0
    institutions: int = 0
    personnel: int = 0
    date_min: str = ""
    date_max: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_VIOLATION_SEPARATORS = ("；", ";", "、", ",", "，", "|")


def split_multi_value(text: str, separators: Sequence[str] = _VIOLATION_SEPARATORS) -> List[str]:
    """把顿号/分号分隔的多值字段拆成列表（去重且保持顺序）。"""
    if not text:
        return []
    normalized = text
    for sep in separators:
        normalized = normalized.replace(sep, "\x00")
    seen: set[str] = set()
    result: List[str] = []
    for part in normalized.split("\x00"):
        item = part.strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


# ──────────────────────────── 目录清单缓存 ────────────────────────────

_catalog_cache: Dict[str, Tuple[Tuple[Any, ...], List[CaseRow]]] = {}
_catalog_lock = threading.RLock()


def _catalog_signature(dataset: str, config: Optional[Config] = None) -> Tuple[Any, ...]:
    """用于判断缓存是否失效的信号（索引文件与摘要目录的变化）。"""
    if dataset == DATASET_AMAC:
        root = amac_summaries_dir(config)
    else:
        root = csrc_summaries_dir(config)
    index_path = root / SummaryIndex.FILENAME
    try:
        stat = index_path.stat()
        signature: Tuple[Any, ...] = (index_path.as_posix(), stat.st_mtime_ns, stat.st_size)
    except OSError:
        signature = (index_path.as_posix(), 0, 0)
    try:
        root_stat = root.stat()
        signature += (root_stat.st_mtime_ns,)
    except OSError:
        signature += (0,)
    return signature


def invalidate_catalog(dataset: Optional[str] = None) -> None:
    """清除目录清单缓存；``dataset`` 为 ``None`` 时清除全部。"""
    with _catalog_lock:
        if dataset is None:
            _catalog_cache.clear()
        else:
            _catalog_cache.pop(dataset, None)


# ──────────────────────────── 清单构建 ────────────────────────────


def _row_from_amac_summary(
    data: Dict[str, Any],
    summary_path: Path,
    status: str,
    config: Optional[Config] = None,
) -> CaseRow:
    category = str(data.get("category", ""))
    case_id = str(data.get("case_id", "") or summary_path.name.replace("_summary.json", ""))
    case_path = amac_case_path(case_id, category, config)
    return CaseRow(
        dataset=DATASET_AMAC,
        case_id=case_id,
        date=str(data.get("date", "")),
        title=str(data.get("title", "")),
        category=category,
        entity=str(data.get("punished_entity", "")),
        entity_type=str(data.get("entity_type", "")) or ({"scfjg": "机构", "scfry": "个人"}.get(category, "")),
        org_type=str(data.get("org_type", "")),
        violation_type=str(data.get("violation_type", "")),
        punishment=str(data.get("punishment", "")),
        involved_fund=str(data.get("involved_fund", "")),
        violation_summary=str(data.get("violation_summary", "")),
        legal_basis=str(data.get("legal_basis", "")),
        source_url=str(data.get("source_url", "")),
        summary_file=summary_path.name,
        case_file=str(case_path) if case_path else "",
        status=status or ("done" if data.get("extract_success") else "failed"),
        error=str(data.get("error", "")),
    )


def _row_from_csrc_summary(
    data: Dict[str, Any],
    summary_path: Path,
    status: str,
    config: Optional[Config] = None,
) -> CaseRow:
    bureau = str(data.get("bureau", ""))
    case_type = str(data.get("case_type", ""))
    case_id = str(data.get("case_id", "") or summary_path.stem.replace("_summary", ""))
    case_path = csrc_case_path(case_id, bureau, case_type, config)
    return CaseRow(
        dataset=DATASET_CSRC,
        case_id=case_id,
        date=str(data.get("date", "")),
        title=str(data.get("title", "")),
        bureau=bureau,
        case_type=case_type,
        entity=str(data.get("punished_entities", "")),
        entity_type=str(data.get("entity_type", "")),
        violation_type=str(data.get("violation_type", "")),
        punishment=str(data.get("punishment", "")),
        involved_fund=str(data.get("involved_fund", "")),
        violation_summary=str(data.get("violation_summary", "")),
        legal_basis=str(data.get("legal_basis", "")),
        penalty_amount=str(data.get("penalty_amount", "")),
        market_ban=str(data.get("market_ban", "")),
        source_url=str(data.get("source_url", "")),
        summary_file=summary_path.name,
        case_file=str(case_path) if case_path else "",
        status=status or ("done" if data.get("extract_success") else "failed"),
        error=str(data.get("error", "")),
    )


def _build_amac_catalog(config: Optional[Config]) -> List[CaseRow]:
    summaries_root = amac_summaries_dir(config)
    index = SummaryIndex(summaries_root)
    rows: List[CaseRow] = []
    for path in iter_summary_files(DATASET_AMAC, config):
        data = read_json(path)
        if not data:
            continue
        case_id = str(data.get("case_id", "") or path.stem.replace("_summary", ""))
        rows.append(_row_from_amac_summary(data, path, index.status_of(case_id), config))
    return rows


def _build_csrc_catalog(config: Optional[Config]) -> List[CaseRow]:
    summaries_root = csrc_summaries_dir(config)
    index = SummaryIndex(summaries_root)
    rows: List[CaseRow] = []
    seen: set[str] = set()

    for path in iter_summary_files(DATASET_CSRC, config):
        data = read_json(path)
        if not data:
            continue
        case_id = str(data.get("case_id", "") or path.stem.replace("_summary", ""))
        seen.add(case_id)
        try:
            relative = path.relative_to(summaries_root).as_posix()
        except ValueError:
            relative = path.name
        row = _row_from_csrc_summary(data, path, index.status_of(case_id), config)
        row.summary_file = relative
        rows.append(row)

    # 被判定为非基金相关的案例没有摘要文件，从索引补建行以便统计与展示
    lookup: Optional[Dict[str, Dict[str, Any]]] = None
    for case_id in index.get_skipped_cases():
        if case_id in seen:
            continue
        info = index.info(case_id)
        content_id = case_id.split("_", 1)[1] if "_" in case_id else case_id
        if lookup is None:
            try:
                lookup = CsrcIndex(csrc_cases_dir(config)).link_lookup_by_content_id()
            except Exception:  # noqa: BLE001 - 索引缺失时降级为无标题行
                lookup = {}
        meta = lookup.get(content_id, {})
        rows.append(
            CaseRow(
                dataset=DATASET_CSRC,
                case_id=case_id,
                date=str(meta.get("date", "")),
                title=str(meta.get("title", "")),
                bureau=str(meta.get("bureau", "")),
                case_type=str(meta.get("case_type", "")),
                source_url=str(meta.get("link_url", "")),
                status="skipped",
                note=str(info.get("reason", "")),
            )
        )

    return rows


def build_catalog(
    dataset: str,
    config: Optional[Config] = None,
    force: bool = False,
) -> List[CaseRow]:
    """构建（并缓存）某数据集的案例清单。

    Args:
        dataset: ``amac`` 或 ``csrc``。
        config: 配置对象，默认使用全局单例。
        force: 为 ``True`` 时忽略缓存强制重建。
    """
    if dataset not in DATASETS:
        raise ValueError(f"未知数据集：{dataset}（可选 {'、'.join(DATASETS)}）")

    signature = _catalog_signature(dataset, config)
    with _catalog_lock:
        cached = _catalog_cache.get(dataset)
        if cached and cached[0] == signature and not force:
            return cached[1]

    builder = _build_amac_catalog if dataset == DATASET_AMAC else _build_csrc_catalog
    rows = builder(config)
    rows.sort(key=lambda row: (row.date, row.case_id), reverse=True)

    with _catalog_lock:
        _catalog_cache[dataset] = (signature, rows)
    logger.info("已载入 %s 案例清单：%d 条", DATASET_LABELS[dataset], len(rows))
    return rows


def build_all_catalogs(
    config: Optional[Config] = None,
    force: bool = False,
) -> List[CaseRow]:
    """返回两个数据集的合并清单（按日期倒序）。"""
    rows: List[CaseRow] = []
    for dataset in DATASETS:
        rows.extend(build_catalog(dataset, config, force=force))
    rows.sort(key=lambda row: (row.date, row.case_id), reverse=True)
    return rows


# ──────────────────────────── 筛选与正文读取 ────────────────────────────


def filter_rows(
    rows: Iterable[CaseRow],
    datasets: Optional[Sequence[str]] = None,
    entity_types: Optional[Sequence[str]] = None,
    violation_types: Optional[Sequence[str]] = None,
    statuses: Optional[Sequence[str]] = None,
    bureaus: Optional[Sequence[str]] = None,
    case_types: Optional[Sequence[str]] = None,
    date_from: str = "",
    date_to: str = "",
    keyword: str = "",
    limit: Optional[int] = None,
) -> List[CaseRow]:
    """按多维条件筛选案例行。

    ``violation_types`` 为「或」语义：命中其中任意一个违规类型即保留；
    ``keyword`` 在标题、当事人、违规摘要、涉及基金中做包含匹配（忽略大小写）。
    """
    dataset_set = set(datasets) if datasets else None
    entity_set = set(entity_types) if entity_types else None
    violation_set = set(violation_types) if violation_types else None
    status_set = set(statuses) if statuses else None
    bureau_set = set(bureaus) if bureaus else None
    case_type_set = set(case_types) if case_types else None
    needle = keyword.strip().lower()

    result: List[CaseRow] = []
    for row in rows:
        if dataset_set and row.dataset not in dataset_set:
            continue
        if entity_set and row.entity_type not in entity_set:
            continue
        if violation_set and not (violation_set & set(row.violation_types)):
            continue
        if status_set and row.status not in status_set:
            continue
        if bureau_set and row.bureau not in bureau_set:
            continue
        if case_type_set and row.case_type not in case_type_set:
            continue
        if date_from and row.date and row.date < date_from:
            continue
        if date_to and row.date and row.date > date_to:
            continue
        if needle:
            haystack = " ".join(
                [row.title, row.entity, row.violation_summary, row.involved_fund, row.case_id]
            ).lower()
            if needle not in haystack:
                continue
        result.append(row)
        if limit is not None and len(result) >= limit:
            break
    return result


def read_case(
    dataset: str,
    case_id: str,
    bureau: str = "",
    case_type: str = "",
    category: str = "",
    config: Optional[Config] = None,
) -> Optional[Dict[str, Any]]:
    """读取案例原文 JSON（含 ``raw_text``）。"""
    path = resolve_case_path(dataset, case_id, bureau, case_type, category, config)
    if path is None:
        logger.warning("未找到案例原文：%s / %s", dataset, case_id)
        return None
    return read_json(path)


def dataset_overview(
    dataset: str,
    config: Optional[Config] = None,
    force: bool = False,
) -> DatasetOverview:
    """汇总某数据集的规模与状态指标。"""
    cfg = config or get_config()
    rows = build_catalog(dataset, cfg, force=force)

    if dataset == DATASET_AMAC:
        cases_root = amac_cases_dir(cfg)
        summaries_root = amac_summaries_dir(cfg)
    else:
        cases_root = csrc_cases_dir(cfg)
        summaries_root = csrc_summaries_dir(cfg)

    cases_root = cfg.resolve_path(cases_root)
    summaries_root = cfg.resolve_path(summaries_root)

    overview = DatasetOverview(
        dataset=dataset,
        label=DATASET_LABELS[dataset],
        case_files=sum(1 for path in cases_root.rglob("*.json") if not path.name.startswith("_")),
        summary_files=sum(1 for path in summaries_root.rglob("*_summary.json") if not path.name.startswith("_")),
    )

    for row in rows:
        if row.status == "done":
            overview.done += 1
        elif row.status == "skipped":
            overview.skipped += 1
        elif row.status == "failed":
            overview.failed += 1
        if row.entity_type == "机构":
            overview.institutions += 1
        elif row.entity_type == "个人":
            overview.personnel += 1

    dates = sorted(row.date for row in rows if row.date)
    if dates:
        overview.date_min = dates[0]
        overview.date_max = dates[-1]
    overview.pending = max(0, overview.case_files - overview.done - overview.skipped - overview.failed)
    return overview

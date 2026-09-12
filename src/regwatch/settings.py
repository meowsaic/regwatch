"""配置管理。

加载优先级（由低到高）::

    内置默认值  <  config.example.json  <  config.json  <  环境变量

``config.json`` 是本地实际配置（含密钥，已加入 .gitignore），
``config.example.json`` 是入库的样例；首次运行时以样例为模板生成。

相比旧版的六键 ``data_roots``，现在只需两个路径：

- ``database`` —— 唯一的 SQLite 库文件
- ``reports_dir`` —— 报告产物目录

读到旧配置时会自动转换并备份原文件（:func:`migrate_legacy_roots`）。
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .domain import ModelProfile

__all__ = [
    "CONFIG_EXAMPLE_FILENAME",
    "CONFIG_FILENAME",
    "MAX_CONCURRENCY",
    "PROJECT_ROOT",
    "TASK_KINDS",
    "TASK_LABELS",
    "ConfigError",
    "ConfigStore",
    "Settings",
    "get_settings",
    "reset_settings",
]

CONFIG_FILENAME = "config.json"
CONFIG_EXAMPLE_FILENAME = "config.example.json"
LEGACY_BACKUP_SUFFIX = ".legacy.bak"

ENV_PREFIX = "REGWATCH_"

#: 并发上限，防止误填过大值打爆目标站点或模型限额
MAX_CONCURRENCY = 64

DEFAULT_DATABASE = "data/regwatch.db"
DEFAULT_REPORTS_DIR = "data/reports"
DEFAULT_CONCURRENCY: dict[str, int] = {"fetch": 8, "summarize": 5}

TASK_KINDS: tuple[str, ...] = ("summarize", "report", "vision", "qa")
TASK_LABELS: dict[str, str] = {
    "summarize": "结构化摘要提取",
    "report": "报告合规建议撰写",
    "vision": "PDF 版式识别（视觉模型）",
    "qa": "智能问答",
}


class ConfigError(RuntimeError):
    """配置缺失或不合法。"""


def _find_project_root(start: Path) -> Path:
    """从包目录向上查找含 ``pyproject.toml`` 的项目根。"""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return start.parent.parent if len(start.parents) >= 2 else start.parent


PROJECT_ROOT = _find_project_root(Path(__file__).resolve().parent)


# ──────────────────────────── 工具函数 ────────────────────────────


def _read_json(path: Path) -> dict[str, Any]:
    """读取 JSON 文件；不存在或损坏时返回空字典。"""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并：``override`` 优先；字典逐层合并，列表整体替换。"""
    merged = dict(base)
    for key, value in (override or {}).items():
        if key.startswith("_"):  # 下划线开头的键是注释
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def migrate_legacy_roots(raw: dict[str, Any]) -> dict[str, Any]:
    """把旧版 ``data_roots`` 六键转换为 ``database`` / ``reports_dir``。

    旧布局 ``data/amac/cases`` → 数据根 ``data`` → 新库 ``data/regwatch.db``。
    旧键会被移除；调用方负责写回磁盘。
    """
    # 只按「是否出现 data_roots」判定，默认值与样例都不会定义这个键
    roots = raw.get("data_roots")
    if not isinstance(roots, dict):
        return raw

    anchor = str(roots.get("amac_cases") or roots.get("csrc_cases") or DEFAULT_DATABASE)
    anchor_path = Path(anchor)
    data_root = anchor_path.parent.parent if len(anchor_path.parts) >= 2 else Path("data")
    if data_root.name in {"cases", "summaries", "reports"}:
        data_root = data_root.parent
    if not str(data_root):
        data_root = Path("data")

    # 统一写成 POSIX 分隔符，便于跨平台迁移同一份配置
    raw["database"] = str(Path(data_root) / "regwatch.db").replace("\\", "/")
    raw["reports_dir"] = str(Path(data_root) / "reports").replace("\\", "/")
    raw.pop("data_roots", None)
    raw["_migrated_from_data_roots"] = True
    return raw


def _normalize_model(raw: Any) -> ModelProfile | None:
    if not isinstance(raw, dict):
        return None
    profile = ModelProfile.from_dict(raw)
    if not profile.id:
        return None
    return profile


# ──────────────────────────── 配置快照 ────────────────────────────


@dataclass(slots=True)
class Settings:
    """一次配置加载的不可变快照。

    所有路径均为绝对路径；模型条目的密钥已应用环境变量覆盖。
    """

    database: Path = field(default_factory=lambda: PROJECT_ROOT / DEFAULT_DATABASE)
    reports_dir: Path = field(default_factory=lambda: PROJECT_ROOT / DEFAULT_REPORTS_DIR)
    concurrency: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_CONCURRENCY))
    models: tuple[ModelProfile, ...] = ()
    tasks: dict[str, str] = field(default_factory=dict)
    source_path: Path = field(default_factory=lambda: PROJECT_ROOT / CONFIG_FILENAME)

    # ── 并发 ──

    def concurrency_for(self, key: str, default: int = 5) -> int:
        """读取并发数并裁剪到 ``[1, MAX_CONCURRENCY]``。"""
        raw = self.concurrency.get(key)
        if raw is None:
            return default
        return max(1, min(MAX_CONCURRENCY, int(raw)))

    # ── 模型 ──

    def model(self, model_id: str) -> ModelProfile | None:
        if not model_id:
            return None
        for profile in self.models:
            if profile.id == model_id:
                return profile
        return None

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(profile.id for profile in self.models)

    def task_model(self, task: str) -> str:
        return self.tasks.get(task, "")

    def resolve_profile(
        self,
        model_id: str | None = None,
        task: str | None = None,
        required: bool = True,
    ) -> ModelProfile | None:
        """按「显式 id → 任务绑定 → 第一条」的顺序解析模型配置。

        Raises:
            ConfigError: ``required`` 为真且无任何可用配置时。
        """
        if model_id:
            profile = self.model(model_id)
            if profile is None:
                raise ConfigError(f"未找到模型配置：{model_id}")
            return profile

        if task:
            bound = self.task_model(task)
            if bound:
                profile = self.model(bound)
                if profile is not None:
                    return profile

        if self.models:
            return self.models[0]

        if required:
            raise ConfigError(
                "尚未配置任何模型。请在网页端「模型与配置」页填写 "
                "接口地址（base_url）、API Key 与模型名称后保存。"
            )
        return None

    def to_raw(self) -> dict[str, Any]:
        """序列化为可写入 ``config.json`` 的原始字典。"""
        return {
            "database": _relativize(self.database),
            "reports_dir": _relativize(self.reports_dir),
            "concurrency": dict(self.concurrency),
            "models": [profile.to_dict() for profile in self.models],
            "tasks": {kind: self.tasks.get(kind, "") for kind in TASK_KINDS},
        }

    def replace(self, **changes: Any) -> Settings:
        return replace(self, **changes)


def _relativize(path: Path) -> str:
    """绝对路径尽量写成相对项目根的形式，便于整个数据目录搬家。"""
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
    except (ValueError, OSError):
        return str(path)


def _absolutize(value: str | Path, fallback: Path) -> Path:
    text = str(value).strip() if value is not None else ""
    if not text:
        return fallback
    path = Path(text)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


# ──────────────────────────── 配置读写 ────────────────────────────


class ConfigStore:
    """``config.json`` 的读写入口。

    线程安全：写操作与快照读取都在可重入锁保护下进行，
    便于 Streamlit 多线程刷新与后台任务并发读取。
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else PROJECT_ROOT / CONFIG_FILENAME
        self._lock = threading.RLock()
        self._settings = self._load()

    # ── 载入 ──

    @property
    def settings(self) -> Settings:
        with self._lock:
            return self._settings

    def reload(self) -> Settings:
        with self._lock:
            self._settings = self._load()
            return self._settings

    def _read_raw(self) -> dict[str, Any]:
        merged = _deep_merge(
            {
                "database": DEFAULT_DATABASE,
                "reports_dir": DEFAULT_REPORTS_DIR,
                "concurrency": dict(DEFAULT_CONCURRENCY),
                "models": [],
                "tasks": {kind: "" for kind in TASK_KINDS},
            },
            _read_json(PROJECT_ROOT / CONFIG_EXAMPLE_FILENAME),
        )
        merged = _deep_merge(merged, _read_json(self.path))
        return migrate_legacy_roots(merged)

    def _load(self) -> Settings:
        raw = self._read_raw()

        migrated = raw.pop("_migrated_from_data_roots", False)
        if migrated:
            self._persist_raw(raw, backup=True)

        models = [self._apply_env_key(profile) for profile in self._normalized_models(raw)]

        tasks_raw = raw.get("tasks") or {}
        tasks = {kind: str(tasks_raw.get(kind) or "") for kind in TASK_KINDS}

        return Settings(
            database=_absolutize(
                str(os.environ.get(f"{ENV_PREFIX}DATABASE") or raw.get("database") or ""),
                PROJECT_ROOT / DEFAULT_DATABASE,
            ),
            reports_dir=_absolutize(
                str(os.environ.get(f"{ENV_PREFIX}REPORTS_DIR") or raw.get("reports_dir") or ""),
                PROJECT_ROOT / DEFAULT_REPORTS_DIR,
            ),
            concurrency=self._normalize_concurrency(raw.get("concurrency")),
            models=tuple(models),
            tasks=tasks,
            source_path=self.path,
        )

    @staticmethod
    def _normalize_concurrency(raw: Any) -> dict[str, int]:
        result = dict(DEFAULT_CONCURRENCY)
        if isinstance(raw, dict):
            for key, value in raw.items():
                try:
                    result[str(key)] = max(1, min(MAX_CONCURRENCY, int(value)))
                except (TypeError, ValueError):
                    continue
        return result

    # ── 持久化 ──

    def _persist_raw(self, raw: dict[str, Any], backup: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if backup and self.path.exists():
            self.path.replace(self.path.with_name(self.path.name + LEGACY_BACKUP_SUFFIX))
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _normalized_models(self, raw: dict[str, Any]) -> tuple[ModelProfile, ...]:
        """解析配置里的模型条目，跳过不合法的项。"""
        models: list[ModelProfile] = []
        for item in raw.get("models") or []:
            profile = _normalize_model(item)
            if profile is not None:
                models.append(profile)
        return tuple(models)

    def _save(self, settings: Settings) -> None:
        with self._lock:
            # 环境变量中的密钥优先级最高，保存后仍要生效
            settings = settings.replace(
                models=tuple(self._apply_env_key(item) for item in settings.models)
            )
            self._settings = settings
            self._persist_raw(settings.to_raw())

    # ── 修改器 ──

    def set_database(self, value: str | Path) -> Settings:
        with self._lock:
            return self._save_and_return(
                self._settings.replace(database=_absolutize(value, self._settings.database))
            )

    def set_reports_dir(self, value: str | Path) -> Settings:
        with self._lock:
            return self._save_and_return(
                self._settings.replace(reports_dir=_absolutize(value, self._settings.reports_dir))
            )

    def set_concurrency(self, key: str, value: int) -> Settings:
        with self._lock:
            concurrency = dict(self._settings.concurrency)
            concurrency[key] = max(1, min(MAX_CONCURRENCY, int(value)))
            return self._save_and_return(self._settings.replace(concurrency=concurrency))

    def upsert_model(self, profile: ModelProfile) -> Settings:
        if not profile.id:
            raise ConfigError("模型配置缺少标识 id")
        with self._lock:
            models = [item for item in self._settings.models if item.id != profile.id]
            models.append(profile)
            tasks = dict(self._settings.tasks)
            # 首个模型自动绑定到摘要任务，避免新用户还要手动选择
            if len(models) == 1 and not tasks.get("summarize"):
                tasks["summarize"] = profile.id
            return self._save_and_return(self._settings.replace(models=tuple(models), tasks=tasks))

    def delete_model(self, model_id: str) -> Settings:
        with self._lock:
            models = tuple(item for item in self._settings.models if item.id != model_id)
            tasks = {
                kind: ("" if bound == model_id else bound)
                for kind, bound in self._settings.tasks.items()
            }
            return self._save_and_return(self._settings.replace(models=models, tasks=tasks))

    def set_task_model(self, task: str, model_id: str) -> Settings:
        if task not in TASK_KINDS:
            raise ConfigError(f"未知任务类型: {task}（可选：{'、'.join(TASK_KINDS)}）")
        with self._lock:
            tasks = dict(self._settings.tasks)
            tasks[task] = model_id or ""
            return self._save_and_return(self._settings.replace(tasks=tasks))

    def _save_and_return(self, settings: Settings) -> Settings:
        self._save(settings)
        return self._settings

    # ── 环境变量 ──

    @staticmethod
    def env_key_name(model_id: str) -> str:
        """模型 ID 对应的环境变量名，如 ``REGWATCH_API_KEY_DEEPSEEK``。"""
        sanitized = re.sub(r"[^A-Za-z0-9]", "_", model_id or "").upper()
        return f"{ENV_PREFIX}API_KEY_{sanitized}"

    @classmethod
    def _apply_env_key(cls, profile: ModelProfile) -> ModelProfile:
        """环境变量中的密钥优先级高于配置文件。"""
        candidates = [profile.api_key_env, cls.env_key_name(profile.id)]
        for name in candidates:
            if not name:
                continue
            value = os.environ.get(name)
            if value and value.strip():
                return replace(profile, api_key=value.strip())
        return profile


# ──────────────────────────── 进程级入口 ────────────────────────────

_store: ConfigStore | None = None
_store_lock = threading.Lock()


def get_settings(path: Path | str | None = None) -> Settings:
    """返回进程级配置快照（首次调用时载入，供入口层薄适配使用）。"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ConfigStore(Path(path) if path else None)
    return _store.settings


def reset_settings(path: Path | str | None = None) -> Settings:
    """重建进程级配置（配置变更或测试时使用）。"""
    global _store
    with _store_lock:
        _store = ConfigStore(Path(path) if path else None)
        return _store.settings


def store(path: Path | str | None = None) -> ConfigStore:
    """返回可写的配置仓储实例（网页端「模型与配置」页使用）。"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ConfigStore(Path(path) if path else None)
    return _store

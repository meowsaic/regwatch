"""统一配置管理。

配置加载优先级（由低到高）::

    内置默认值  <  config.example.json  <  config.json  <  环境变量

``config.json`` 是本地实际配置（含密钥，已加入 .gitignore），
``config.example.json`` 是入库的样例，首次运行时会以它为模板自动生成
``config.json``，用户只需在网页端「模型配置」页填写 base_url / api_key。

模型条目（``models``）与任务（``tasks``）解耦：一条模型配置可被多个任务复用，
``tasks`` 中的值指向 ``models[].id``。
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from .datamodels import ModelProfile

__all__ = [
    "CONFIG_FILENAME",
    "DATA_ROOT_LABELS",
    "DEFAULT_DATA_ROOTS",
    "PROJECT_ROOT",
    "TASK_KINDS",
    "TASK_LABELS",
    "Config",
    "ConfigError",
    "get_config",
    "reset_config",
]

# ──────────────────────────── 常量 ────────────────────────────


def _find_project_root(start: Path) -> Path:
    """从包目录向上查找含 ``pyproject.toml`` 的项目根。

    源码布局（``src/regwatch/``）与可编辑安装均适用；找不到时回退到
    ``start`` 的上两级，保证导入不因布局变化而崩溃。
    """
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return start.parent.parent if len(start.parents) >= 2 else start.parent


PROJECT_ROOT = _find_project_root(Path(__file__).resolve().parent)

CONFIG_FILENAME = "config.json"
CONFIG_EXAMPLE_FILENAME = "config.example.json"

_ENV_PREFIX = "REGWATCH_"

DEFAULT_DATA_ROOTS: dict[str, str] = {
    "amac_cases": "data/amac/cases",
    "amac_summaries": "data/amac/summaries",
    "amac_reports": "data/amac/reports",
    "csrc_cases": "data/csrc/cases",
    "csrc_summaries": "data/csrc/summaries",
    "csrc_reports": "data/csrc/reports",
}

DATA_ROOT_LABELS: dict[str, str] = {
    "amac_cases": "AMAC 案例原文",
    "amac_summaries": "AMAC 结构化摘要",
    "amac_reports": "AMAC 报告产物",
    "csrc_cases": "CSRC 案例原文",
    "csrc_summaries": "CSRC 结构化摘要",
    "csrc_reports": "CSRC 报告产物",
}

DEFAULT_CONCURRENCY: dict[str, int] = {"fetch": 8, "summarize": 5}

TASK_KINDS = ("summarize", "report", "vision")

TASK_LABELS: dict[str, str] = {
    "summarize": "结构化摘要提取",
    "report": "报告合规建议撰写",
    "vision": "PDF 版式识别（视觉模型）",
}

_MAX_CONCURRENCY = 64


class ConfigError(RuntimeError):
    """配置缺失或不合法。"""


# ──────────────────────────── 辅助函数 ────────────────────────────


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
    """递归合并字典：``override`` 优先；字典逐层合并，列表整体替换。"""
    merged = dict(base)
    for key, value in (override or {}).items():
        if key.startswith("_"):  # 下划线开头的键是注释，不外泄
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_model(raw: dict[str, Any]) -> dict[str, Any]:
    """规范化单条模型配置，补齐默认值。"""
    profile = ModelProfile.from_dict(raw)
    data = profile.to_dict()
    data.pop("display_name", None)
    if not isinstance(data.get("extra"), dict):
        data["extra"] = {}
    if data.get("token_param") not in ("max_tokens", "max_completion_tokens"):
        data["token_param"] = "max_tokens"
    if not data.get("vision_content_type"):
        data["vision_content_type"] = "image_url"
    return data


def _default_data() -> dict[str, Any]:
    return {
        "data_roots": dict(DEFAULT_DATA_ROOTS),
        "concurrency": dict(DEFAULT_CONCURRENCY),
        "models": [],
        "tasks": {kind: "" for kind in TASK_KINDS},
    }


# ──────────────────────────── 配置对象 ────────────────────────────


class Config:
    """配置读写与查询。

    线程安全：所有写操作与快照读取都在可重入锁保护下进行，
    便于 Streamlit 多线程刷新与后台任务并发读取。
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else PROJECT_ROOT / CONFIG_FILENAME
        self._lock = threading.RLock()
        self.data: dict[str, Any] = self._load()

    # ── 载入 / 保存 ──

    def _load(self) -> dict[str, Any]:
        merged = _default_data()
        merged = _deep_merge(merged, _read_json(PROJECT_ROOT / CONFIG_EXAMPLE_FILENAME))
        merged = _deep_merge(merged, _read_json(self.path))
        models = merged.get("models")
        merged["models"] = (
            [_normalize_model(item) for item in models if isinstance(item, dict)]
            if isinstance(models, list)
            else []
        )
        tasks = merged.get("tasks")
        merged["tasks"] = {kind: str((tasks or {}).get(kind) or "") for kind in TASK_KINDS}
        return merged

    def reload(self) -> Config:
        """从磁盘重新载入配置。"""
        with self._lock:
            self.data = self._load()
        return self

    def exists_on_disk(self) -> bool:
        return self.path.exists()

    def save(self) -> Path:
        """原子写入 ``config.json``。"""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
            return self.path

    def snapshot(self) -> dict[str, Any]:
        """返回配置的深拷贝，避免调用方直接改动内部状态。"""
        with self._lock:
            return json.loads(json.dumps(self.data, ensure_ascii=False))

    # ── 数据根目录 ──

    def data_roots(self) -> dict[str, Path]:
        """返回全部数据根目录的绝对路径。"""
        raw = self.data.get("data_roots") or {}
        roots: dict[str, Path] = {}
        for key, default in DEFAULT_DATA_ROOTS.items():
            value = raw.get(key) or default
            roots[key] = self.resolve_path(value)
        return roots

    def data_root(self, key: str) -> Path:
        roots = self.data_roots()
        if key not in roots:
            raise ConfigError(f"未知数据根目录: {key}（可选：{'、'.join(sorted(roots))}）")
        return roots[key]

    def set_data_root(self, key: str, value: str) -> None:
        if key not in DEFAULT_DATA_ROOTS:
            raise ConfigError(f"未知数据根目录: {key}")
        with self._lock:
            self.data.setdefault("data_roots", {})[key] = value
        self.save()

    @staticmethod
    def resolve_path(value: str | Path) -> Path:
        """把配置中的相对路径解析为项目根目录下的绝对路径。"""
        path = Path(value)
        return path if path.is_absolute() else (PROJECT_ROOT / path)

    # ── 并发 ──

    def concurrency(self, key: str, default: int = 5) -> int:
        """读取并发数，自动裁剪到合法区间 ``[1, 64]``。"""
        raw = (self.data.get("concurrency") or {}).get(key)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return max(1, min(_MAX_CONCURRENCY, value))

    def set_concurrency(self, key: str, value: int) -> None:
        with self._lock:
            self.data.setdefault("concurrency", {})[key] = max(1, min(_MAX_CONCURRENCY, int(value)))
        self.save()

    # ── 模型条目 ──

    def models(self) -> list[ModelProfile]:
        """返回全部模型配置（已应用环境变量覆盖）。"""
        with self._lock:
            raw_models = list(self.data.get("models") or [])
        return [self._apply_env_key(ModelProfile.from_dict(item)) for item in raw_models]

    def model_ids(self) -> list[str]:
        return [profile.id for profile in self.models()]

    def get_model(self, model_id: str) -> ModelProfile | None:
        if not model_id:
            return None
        for profile in self.models():
            if profile.id == model_id:
                return profile
        return None

    def upsert_model(self, profile: ModelProfile) -> None:
        """新增或按 ``id`` 覆盖一条模型配置。"""
        if not profile.id:
            raise ConfigError("模型配置缺少标识 id")
        with self._lock:
            models = list(self.data.get("models") or [])
            record = profile.to_dict()
            for index, item in enumerate(models):
                if isinstance(item, dict) and item.get("id") == profile.id:
                    models[index] = record
                    break
            else:
                models.append(record)
            self.data["models"] = models

            # 首个模型自动绑定到摘要任务，避免新用户还要手动选择
            tasks = self.data.setdefault("tasks", {})
            if len(models) == 1 and not tasks.get("summarize"):
                tasks["summarize"] = profile.id
        self.save()

    def delete_model(self, model_id: str) -> bool:
        """删除模型配置；同时清理指向它的任务绑定。"""
        removed = False
        with self._lock:
            models = list(self.data.get("models") or [])
            remaining = [
                item
                for item in models
                if not (isinstance(item, dict) and item.get("id") == model_id)
            ]
            removed = len(remaining) != len(models)
            self.data["models"] = remaining
            tasks = self.data.setdefault("tasks", {})
            for kind, bound in list(tasks.items()):
                if bound == model_id:
                    tasks[kind] = ""
        if removed:
            self.save()
        return removed

    # ── 任务 → 模型映射 ──

    def task_model_id(self, task: str) -> str:
        return str((self.data.get("tasks") or {}).get(task) or "")

    def task_models(self) -> dict[str, str]:
        return {kind: self.task_model_id(kind) for kind in TASK_KINDS}

    def set_task_model(self, task: str, model_id: str) -> None:
        if task not in TASK_KINDS:
            raise ConfigError(f"未知任务类型: {task}（可选：{'、'.join(TASK_KINDS)}）")
        with self._lock:
            self.data.setdefault("tasks", {})[task] = model_id or ""
        self.save()

    def resolved_profile(
        self,
        model_id: str | None = None,
        task: str | None = None,
        required: bool = True,
    ) -> ModelProfile | None:
        """解析出实际要使用的模型配置。

        查找顺序：显式 ``model_id`` → ``task`` 绑定 → 第一条模型配置。

        Raises:
            ConfigError: 当 ``required`` 为 ``True`` 且无任何可用配置时。
        """
        if model_id:
            profile = self.get_model(model_id)
            if profile is not None:
                return profile
            raise ConfigError(f"未找到模型配置：{model_id}")

        if task:
            bound = self.task_model_id(task)
            if bound:
                profile = self.get_model(bound)
                if profile is not None:
                    return profile

        models = self.models()
        if models:
            return models[0]

        if required:
            raise ConfigError(
                "尚未配置任何模型。请在网页端「模型配置」页填写 "
                "接口地址（base_url）、API Key 与模型名称后保存。"
            )
        return None

    # ── 环境变量覆盖 ──

    @staticmethod
    def env_key_name(model_id: str) -> str:
        """模型 ID 对应的环境变量名，如 ``REGWATCH_API_KEY_DEEPSEEK``。"""
        sanitized = re.sub(r"[^A-Za-z0-9]", "_", model_id or "").upper()
        return f"{_ENV_PREFIX}API_KEY_{sanitized}"

    def _apply_env_key(self, profile: ModelProfile) -> ModelProfile:
        """环境变量中的密钥优先级高于配置文件。"""
        candidates: list[str] = []
        if profile.api_key_env:
            candidates.append(profile.api_key_env)
        candidates.append(self.env_key_name(profile.id))
        for name in candidates:
            value = os.environ.get(name)
            if value and value.strip():
                return replace(profile, api_key=value.strip())
        return profile


# ──────────────────────────── 单例 ────────────────────────────

_config: Config | None = None
_singleton_lock = threading.Lock()


def get_config(path: Path | None = None) -> Config:
    """返回全局配置单例（首次调用时创建）。"""
    global _config
    if _config is None:
        with _singleton_lock:
            if _config is None:
                _config = Config(path)
    return _config


def reset_config(path: Path | None = None) -> Config:
    """重建全局配置单例（配置变更或测试时使用）。"""
    global _config
    with _singleton_lock:
        _config = Config(path)
    return _config

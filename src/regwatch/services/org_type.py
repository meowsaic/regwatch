"""机构登记类型解析与回填服务。

职责：

1. **机构名称提取**：从纪律处分标题 / 决定书正文中定位受处分机构的完整注册名称。
2. **登记类型查询**：按「库内缓存 → 中基协活跃管理人接口 → 已注销管理人接口 →
   正文正则」四级降级解析机构登记类型。
3. **回填与人工补全**：为历史案例补齐 ``org_type`` / ``punished_entity``，
   并支持交互式人工指定与详情页 URL 自动提取。

网络请求经由 :class:`~regwatch.sources.http.HttpClient` 注入，
模型调用经由 :class:`~regwatch.llm.LLMClientFactory` 注入，
缓存落在 :class:`~regwatch.db.repositories.MetaRepository`。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ..clock import now_iso
from ..domain import CaseRecord, Category, Dataset
from ..llm import LLMClientFactory, LLMError
from ..logging_setup import get_logger
from ..sources.http import DEFAULT_USER_AGENT, HttpClient, RequestsHttpClient

logger = get_logger("org_type")

__all__ = [
    "AMAC_CANCELLED_API",
    "AMAC_MANAGER_API",
    "ORG_TYPE_VALUES",
    "BackfillResult",
    "OrgTypeService",
    "extract_full_org_name_from_text",
    "extract_org_name_from_title",
    "extract_org_type_from_text",
    "resolve_org_type",
]

AMAC_MANAGER_API = "https://gs.amac.org.cn/amac-infodisc/api/pof/manager/query"
AMAC_CANCELLED_API = "https://gs.amac.org.cn/amac-infodisc/api/cancelled/manager"
AMAC_CANCELLED_DETAIL_URL = "https://gs.amac.org.cn/amac-infodisc/res/cancelled/manager"

_JSON_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
    "Host": "gs.amac.org.cn",
    "Origin": "https://gs.amac.org.cn",
    "User-Agent": DEFAULT_USER_AGENT,
    "X-Requested-With": "XMLHttpRequest",
}
AMAC_MANAGER_HEADERS = {
    **_JSON_HEADERS,
    "Referer": "https://gs.amac.org.cn/amac-infodisc/res/pof/manager/managerList.html",
}
AMAC_CANCELLED_HEADERS = {
    **_JSON_HEADERS,
    "Referer": "https://gs.amac.org.cn/amac-infodisc/res/cancelled/manager/index.html",
}

ORG_TYPE_VALUES: list[str] = [
    "私募证券投资基金管理人",
    "私募股权、创业投资基金管理人",
    "私募股权投资基金管理人",
    "创业投资基金管理人",
    "其他私募投资基金管理人",
    "私募资产配置类管理人",
]

#: 仅由通用后缀组成、无法定位到具体机构的「名字」
_GENERIC_SUFFIXES = (
    "有限公司",
    "股份有限公司",
    "投资有限公司",
    "管理有限公司",
    "基金管理有限公司",
    "资本管理有限公司",
    "资产管理有限公司",
)

_TITLE_SUFFIXES = (
    "的纪律处分决定书",
    "纪律处分决定书",
    "的决定书",
    "决定书",
    "送达公告",
    "（送达公告）",
    "(送达公告)",
    "事先告知书",
    "复核决定书",
)

_NAME_HINT = r"(?:公司|企业|基金|合伙|中心|集团|事务所|有限|资本|投资)"

CANCELLED_API_MAX_RETRIES = 3
CANCELLED_API_RETRY_DELAY = 3


# ──────────────────────────── 机构名称提取 ────────────────────────────


def extract_org_name_from_title(title: str) -> str | None:
    """从纪律处分标题中提取机构名称。"""
    for pattern in (
        r"关于对[《]?([^》]+?)[》]?(?:的)?(?:纪律处分|撤销|注销|暂停|取消)",
        r"关于对[《]?([^》]+?)[》]?的",
    ):
        match = re.search(pattern, title)
        if match:
            name = match.group(1).strip()
            for suffix in _TITLE_SUFFIXES:
                name = name.replace(suffix, "")
            name = name.rstrip("的、，,")
            if len(name) >= 4:
                return name

    match = re.search(r"[（(]((?:[^()（）]|[（(][^)）]*[)）])+)[)）]", title)
    if match:
        for part in re.split(r"[、，,]", match.group(1).strip()):
            part = part.strip()
            if re.search(_NAME_HINT, part) and len(part) >= 4:
                return part

    match = re.search(r"([^\s,，、（）()]+(?:" + _NAME_HINT + r"))", title)
    if match:
        name = match.group(1).strip()
        if len(name) >= 4:
            return name
    return None


def extract_full_org_name_from_text(short_name: str | None, raw_text: str) -> str | None:
    """从正文正则提取机构完整名称（当标题只有简称时使用）。"""
    if not raw_text or len(raw_text) < 10:
        return None

    snippet = raw_text[:3000]
    prefixes = ("申请人", "被申请人", "被处分机构", "当事人")

    def strip_prefix(name: str) -> str:
        for prefix in prefixes:
            for separator in ("：", ":"):
                full = f"{prefix}{separator}"
                if name.startswith(full):
                    name = name[len(full) :].strip()
        return name

    if short_name:
        match = re.search(r"([^，,：:；;\n]+)（以下简称" + re.escape(short_name) + r"）", snippet)
        if match:
            name = strip_prefix(match.group(1).strip())
            if len(name) >= 4 and re.search(r"(?:公司|企业|有限|合伙|事务所|集团)", name):
                return name

    for prefix in prefixes:
        match = re.search(
            prefix + r"[：:]\s*([^\n,，。；;（(]+?(?:公司|企业|有限|合伙|事务所|集团)[^\n]*?)"
            r"(?:[，,。\n（(]|$)",
            snippet,
        )
        if match and len(match.group(1).strip()) >= 4:
            return match.group(1).strip()

    for prefix in prefixes:
        match = re.search(
            prefix + r"[：:]\s*([^\n，,。；;]+?(?:有限公司|股份有限公司|企业|合伙|事务所|集团))",
            snippet,
        )
        if match and len(match.group(1).strip()) >= 4:
            return match.group(1).strip()

    match = re.search(
        r"([^，,：:；;\n]+?(?:公司|企业|有限|合伙|事务所|集团)[^，,：:；;\n]*?)（以下简称", snippet
    )
    if match:
        name = strip_prefix(match.group(1).strip())
        if len(name) >= 4:
            return name

    return None


def extract_org_name_from_text_llm(
    raw_text: str, llm: LLMClientFactory | None = None
) -> str | None:
    """使用大模型从正文提取受处分机构完整名称（降级方案）。"""
    if not raw_text or len(raw_text) < 100 or llm is None:
        return None

    prompt = (
        "请从以下纪律处分决定书文本中，提取被处分机构的完整注册名称。\n"
        '请只输出机构完整名称，不要输出其他内容。如果文本中没有提及，请输出"未知"。\n\n'
        "文本内容：\n" + raw_text[:2000]
    )
    try:
        result = llm.client(task="summarize").chat_text(prompt, max_tokens=100, temperature=0.1)
        cleaned = result.strip("「」《》\"'· \n")
        if cleaned and cleaned != "未知" and len(cleaned) >= 4:
            return cleaned
    except LLMError as exc:
        logger.warning("  [机构名称LLM] 提取失败: %s", exc)
    except Exception as exc:
        logger.warning("  [机构名称LLM] 提取异常: %s", exc)
    return None


def extract_punished_entity(
    title: str,
    raw_text: str,
    llm: LLMClientFactory | None = None,
) -> str:
    """提取受处分机构完整名称，三级策略：标题 → 正文正则 → 模型。"""
    title_name = extract_org_name_from_title(title)
    if title_name and re.search(r"(?:公司|企业|有限|合伙|事务所|集团)", title_name):
        return title_name

    if raw_text:
        full_name = extract_full_org_name_from_text(title_name, raw_text)
        if full_name:
            logger.info("  [机构名称正则] %s → %s", title_name or "?", full_name)
            return full_name

        if len(raw_text) >= 100:
            full_name = extract_org_name_from_text_llm(raw_text, llm)
            if full_name:
                logger.info("  [机构名称LLM] %s → %s", title_name or "?", full_name)
                return full_name

    return title_name or ""


# ──────────────────────────── 登记类型查询 ────────────────────────────


def extract_org_type_from_text(org_name: str, raw_text: str) -> str | None:
    """从案例正文中正则匹配机构类型（仅当正文恰好包含类型字符串时）。"""
    if not raw_text or len(raw_text) < 100:
        return None
    snippet = raw_text[:3000]
    for org_type in ORG_TYPE_VALUES:
        if org_type in snippet:
            logger.info("  [机构类型文本] %s → %s (正则匹配)", org_name, org_type)
            return org_type
    return None


def _match_content(data: Any, org_name: str, name_key: str) -> dict[str, Any] | None:
    content = (data or {}).get("content") or []
    items = [(item, re.sub(r"<[^>]+>", "", str(item.get(name_key, "")))) for item in content]
    for item, clean in items:
        if org_name == clean:
            return item
    for item, clean in items:
        if org_name in clean or clean in org_name:
            return item
    return None


def query_org_type_from_amac(org_name: str, http: HttpClient) -> str | None:
    """从中基协活跃管理人接口查询机构类型。"""
    if len(org_name) < 4 or org_name in _GENERIC_SUFFIXES:
        return None

    payload = {
        "keyword": org_name,
        "establishDate": {"from": "1900-01-01", "to": "9999-01-01"},
        "registerDate": {"from": "1900-01-01", "to": "9999-01-01"},
    }
    try:
        data = http.post_json(
            f"{AMAC_MANAGER_API}?page=0&size=10",
            payload=payload,
            headers=AMAC_MANAGER_HEADERS,
        )
    except Exception as exc:
        logger.warning("  [机构类型API] 查询异常: %s", exc)
        return None

    if not data or not data.get("totalElements"):
        return None
    item = _match_content(data, org_name, "managerName")
    org_type = str((item or {}).get("primaryInvestType") or "")
    if org_type:
        logger.info("  [机构类型API] %s → %s", org_name, org_type)
        return org_type
    return None


def query_org_type_from_amac_cancelled(
    org_name: str,
    http: HttpClient,
    *,
    max_retries: int = CANCELLED_API_MAX_RETRIES,
    retry_delay: float = CANCELLED_API_RETRY_DELAY,
) -> str | None:
    """从中基协已注销管理人接口查询机构类型（注销前登记类型）。

    该搜索接口受 WAF 保护，脚本调用多数时候返回 400，因此带重试。
    """
    if len(org_name) < 4 or org_name in _GENERIC_SUFFIXES:
        return None

    import random

    for attempt in range(1, max_retries + 1):
        try:
            url = f"{AMAC_CANCELLED_API}?rand={random.random()}&page=0&size=10"
            data = http.post_json(
                url, payload={"keyword": org_name}, headers=AMAC_CANCELLED_HEADERS
            )
        except Exception as exc:
            logger.debug("  [已注销API] 第%d次查询异常: %s", attempt, exc)
            if attempt < max_retries:
                time.sleep(retry_delay)
            continue

        if not data or not data.get("totalElements"):
            return None

        item = _match_content(data, org_name, "orgName")
        tenant_id = str((item or {}).get("userTenantId") or "")
        if not tenant_id:
            return None

        try:
            detail = http.get_text(
                f"{AMAC_CANCELLED_DETAIL_URL}/{tenant_id}.html",
                headers={"User-Agent": DEFAULT_USER_AGENT},
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("  [已注销详情] 页面获取失败: %s", exc)
            return None

        match = re.search(r"机构类型.*?<td[^>]*>(.*?)</td>", detail, re.S)
        if match and match.group(1).strip():
            org_type = match.group(1).strip()
            logger.info("  [已注销API] %s → %s", org_name, org_type)
            return org_type
        return None

    return None


def extract_org_type_from_url(url: str, http: HttpClient) -> str:
    """从已注销管理人详情页 URL 中提取机构类型；失败返回空串。"""
    try:
        page = http.get_text(url, headers={"User-Agent": DEFAULT_USER_AGENT}, encoding="utf-8")
    except Exception as exc:
        logger.warning("  [机构类型URL] 请求失败: %s", exc)
        return ""
    match = re.search(r"机构类型.*?<td[^>]*>(.*?)</td>", page, re.S)
    if match:
        return match.group(1).strip()
    logger.warning("  [机构类型URL] 页面中未找到「机构类型」字段")
    return ""


def resolve_org_type(
    title: str,
    raw_text: str,
    *,
    punished_entity: str = "",
    cache_get: Callable[[str], str] | None = None,
    cache_set: Callable[[str, str], None] | None = None,
    http: HttpClient | None = None,
) -> str:
    """解析机构类型，四级降级：缓存 → 活跃接口 → 已注销接口 → 正文正则。"""
    org_name = punished_entity or extract_org_name_from_title(title)
    if not org_name:
        logger.debug("  [机构类型] 无法提取机构名: %s", title)
        return ""

    if cache_get is not None:
        cached = cache_get(org_name)
        if cached:
            logger.info("  [机构类型缓存] %s → %s", org_name, cached)
            return cached

    client = http or RequestsHttpClient()
    org_type = (
        query_org_type_from_amac(org_name, client)
        or query_org_type_from_amac_cancelled(org_name, client)
        or extract_org_type_from_text(org_name, raw_text)
        or ""
    )

    if org_type and cache_set is not None:
        cache_set(org_name, org_type)
    return org_type


# ──────────────────────────── 回填服务 ────────────────────────────


@dataclass
class BackfillResult:
    """一次机构类型回填任务的汇总结果。"""

    total: int = 0
    already_have: int = 0
    org_type_filled: int = 0
    entity_filled: int = 0
    unresolved: int = 0
    manual_list: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "already_have": self.already_have,
            "org_type_filled": self.org_type_filled,
            "entity_filled": self.entity_filled,
            "unresolved": self.unresolved,
            "manual_count": len(self.manual_list),
            "dry_run": self.dry_run,
        }


ProgressHook = Callable[[int, int, str], None]


class OrgTypeService:
    """机构类型回填用例。"""

    def __init__(
        self,
        cases: Any,
        meta: Any,
        llm: LLMClientFactory | None = None,
        *,
        http: HttpClient | None = None,
        request_delay: float = 1.5,
        on_data_changed: Callable[[], None] | None = None,
    ) -> None:
        self._cases = cases
        self._meta = meta
        self._llm = llm
        self._http = http
        self.request_delay = max(0.0, float(request_delay))
        self._on_data_changed = on_data_changed

    # ── 遍历 ──

    def iter_institution_cases(self) -> Iterator[CaseRecord]:
        """逐个产出 AMAC 机构类案例（含正文）。"""
        for case_id in self._cases.ids(Dataset.AMAC):
            case = self._cases.get_with_body(Dataset.AMAC, case_id)
            if case is not None and case.category is Category.INSTITUTION:
                yield case

    # ── 回填 ──

    def backfill(
        self,
        *,
        dry_run: bool = False,
        limit: int | None = None,
        on_progress: ProgressHook | None = None,
    ) -> BackfillResult:
        """为历史 AMAC 机构类案例补齐 ``org_type`` 与 ``punished_entity``。"""
        items = list(self.iter_institution_cases())
        if limit:
            items = items[:limit]

        result = BackfillResult(total=len(items), dry_run=dry_run)
        logger.info(
            "扫描到 %d 个机构类案例，开始%s", result.total, "试算（dry-run）" if dry_run else "回填"
        )

        for index, case in enumerate(items, 1):
            if on_progress:
                on_progress(index, len(items), case.case_id)

            if case.org_type:
                result.already_have += 1
                continue

            if not case.punished_entity:
                case.punished_entity = extract_punished_entity(case.title, case.raw_text, self._llm)
                if case.punished_entity:
                    result.entity_filled += 1

            org_type = resolve_org_type(
                case.title,
                case.raw_text,
                punished_entity=case.punished_entity,
                cache_get=self._meta.org_type,
                cache_set=lambda name, value: self._meta.set_org_type(name, value, "amac_api"),
                http=self._http,
            )

            if org_type:
                result.org_type_filled += 1
                case.org_type = org_type
                logger.info("[%d/%d] %s → %s", index, len(items), case.case_id, org_type)
            else:
                result.unresolved += 1
                logger.warning("[%d/%d] %s → 无法自动获取机构类型", index, len(items), case.case_id)
                result.manual_list.append(
                    {
                        "case_id": case.case_id,
                        "title": case.title,
                        "punished_entity": case.punished_entity,
                    }
                )

            if not dry_run and org_type:
                case.fetch_time = case.fetch_time or now_iso()
                self._cases.upsert(case)
                if self.request_delay:
                    time.sleep(self.request_delay)

        if not dry_run and self._on_data_changed:
            self._on_data_changed()

        logger.info(
            "回填完成：已有 %d，补全机构类型 %d，补全机构名称 %d，未解析 %d",
            result.already_have,
            result.org_type_filled,
            result.entity_filled,
            result.unresolved,
        )
        return result

    # ── 供采集器复用的便捷方法 ──

    def extract_entity(self, title: str, raw_text: str) -> str:
        """提取受处分机构完整名称（三级降级）。"""
        return extract_punished_entity(title, raw_text, self._llm)

    def resolve(self, title: str, raw_text: str, punished_entity: str = "") -> str:
        """解析机构登记类型（四级降级，结果入库缓存）。"""
        return resolve_org_type(
            title,
            raw_text,
            punished_entity=punished_entity,
            cache_get=self._meta.org_type,
            cache_set=lambda name, value: self._meta.set_org_type(name, value, "amac_api"),
            http=self._http,
        )

    # ── 交互式补全 ──

    def interactive_fill(
        self,
        *,
        input_func: Callable[[str], str] = input,
        output_func: Callable[[str], None] = print,
    ) -> tuple[int, int]:
        """交互式补全缺失的机构类型，返回 ``(已补全, 已跳过)``。

        输入规则：编号选择预置类型 | 直接输入自定义类型 | 粘贴详情页 URL 自动提取
        | 回车跳过 | ``q`` 退出。
        """
        missing = [case for case in self.iter_institution_cases() if not case.org_type]
        if not missing:
            output_func("\n所有机构类案例均已拥有 org_type，无需补全。")
            return 0, 0

        output_func(f"\n发现 {len(missing)} 个案例缺少 org_type，开始交互式补全")
        output_func(
            "  输入编号选择类型 | 直接输入自定义类型 | 粘贴详情页URL自动提取 | 回车跳过 | q 退出\n"
        )
        for index, value in enumerate(ORG_TYPE_VALUES, 1):
            output_func(f"  {index}. {value}")
        output_func("")

        filled = skipped = 0
        for index, case in enumerate(missing, 1):
            output_func("-" * 50)
            output_func(f"  [{index}/{len(missing)}] 案例: {case.case_id}")
            output_func(f"  机构: {case.punished_entity or '(无机构名称)'}")
            output_func(f"  标题: {case.title}")

            while True:
                try:
                    choice = input_func("  机构类型 (编号/自定义/URL/回车跳过/q退出): ").strip()
                except (EOFError, KeyboardInterrupt):
                    output_func(f"\n交互式补全结束。已补全 {filled} 条，跳过 {skipped} 条。")
                    return filled, skipped

                if choice.lower() == "q":
                    output_func(f"\n交互式补全结束。已补全 {filled} 条，跳过 {skipped} 条。")
                    return filled, skipped

                if not choice:
                    skipped += 1
                    output_func("  → 跳过")
                    break

                if choice.startswith("http"):
                    org_type = extract_org_type_from_url(choice, self._http or RequestsHttpClient())
                    if not org_type:
                        continue
                    output_func(f"  → 从URL提取: {org_type}")
                elif choice.isdigit() and 1 <= int(choice) <= len(ORG_TYPE_VALUES):
                    org_type = ORG_TYPE_VALUES[int(choice) - 1]
                else:
                    org_type = choice

                case.org_type = org_type
                self._cases.upsert(case)
                if case.punished_entity:
                    self._meta.set_org_type(case.punished_entity, org_type, "manual")
                filled += 1
                output_func(f"  ✓ 已保存: {org_type}")
                break

        output_func(f"\n交互式补全结束。已补全 {filled} 条，跳过 {skipped} 条。")
        return filled, skipped

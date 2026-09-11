"""机构登记类型解析与回填。

职责：

1. **机构名称提取**：从纪律处分标题 / 决定书正文中定位受处分机构的完整注册名称。
2. **登记类型查询**：按「本地缓存 → 中基协活跃管理人接口 → 已注销管理人接口 →
   正文正则」四级降级解析机构登记类型。
3. **回填与人工补全**：为历史案例补齐 ``org_type`` / ``punished_entity`` 字段，
   并支持交互式人工指定与详情页 URL 自动提取。

模型调用统一走 :mod:`regwatch.llm`，缓存与落盘统一走 :mod:`regwatch.storage`。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import requests

from .config import Config, get_config
from .llm import LLMError, get_llm
from .logutil import get_logger
from .storage import OrgTypeCache, read_json, write_json

logger = get_logger("org_type")

AMAC_MANAGER_API = "https://gs.amac.org.cn/amac-infodisc/api/pof/manager/query"
AMAC_MANAGER_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
    "Host": "gs.amac.org.cn",
    "Origin": "https://gs.amac.org.cn",
    "Referer": "https://gs.amac.org.cn/amac-infodisc/res/pof/manager/managerList.html",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

AMAC_CANCELLED_API = "https://gs.amac.org.cn/amac-infodisc/api/cancelled/manager"
AMAC_CANCELLED_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
    "Host": "gs.amac.org.cn",
    "Origin": "https://gs.amac.org.cn",
    "Referer": "https://gs.amac.org.cn/amac-infodisc/res/cancelled/manager/index.html",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}
AMAC_CANCELLED_DETAIL_URL = "https://gs.amac.org.cn/amac-infodisc/res/cancelled/manager"

ORG_TYPE_VALUES = [
    "私募证券投资基金管理人",
    "私募股权、创业投资基金管理人",
    "私募股权投资基金管理人",
    "创业投资基金管理人",
    "其他私募投资基金管理人",
    "私募资产配置类管理人",
]



def extract_org_name_from_title(title: str) -> Optional[str]:
    """从纪律处分标题中提取机构名称"""
    patterns = [
        r"关于对[《]?([^》]+?)[》]?(?:的)?(?:纪律处分|撤销|注销|暂停|取消)",
        r"关于对[《]?([^》]+?)[》]?的",
    ]
    for pattern in patterns:
        m = re.search(pattern, title)
        if m:
            name = m.group(1).strip()
            for suffix in ["的纪律处分决定书", "纪律处分决定书", "的决定书", "决定书",
                           "送达公告", "（送达公告）", "(送达公告)",
                           "事先告知书", "复核决定书"]:
                name = name.replace(suffix, "")
            name = name.rstrip("的、，,")
            if len(name) >= 4:
                return name

    m = re.search(r"[（(]((?:[^()（）]|[（(][^)）]*[)）])+)[)）]", title)
    if m:
        inner = m.group(1).strip()
        parts = re.split(r"[、，,]", inner)
        for part in parts:
            part = part.strip()
            if re.search(r"(?:公司|企业|基金|合伙|中心|集团|事务所|有限|资本|投资)", part):
                if len(part) >= 4:
                    return part

    m = re.search(r"([^\s,，、（）\(\)]+(?:公司|企业|基金|合伙|中心|集团|事务所|有限|资本))", title)
    if m:
        name = m.group(1).strip()
        if len(name) >= 4:
            return name
    return None


def query_org_type_from_amac(org_name: str) -> Optional[str]:
    """从中基协活跃管理人API查询机构类型"""
    if len(org_name) < 4:
        return None

    generic_suffixes = ["有限公司", "股份有限公司", "投资有限公司", "管理有限公司",
                        "基金管理有限公司", "资本管理有限公司", "资产管理有限公司"]
    if org_name in generic_suffixes:
        return None

    try:
        payload = {
            "keyword": org_name,
            "establishDate": {"from": "1900-01-01", "to": "9999-01-01"},
            "registerDate": {"from": "1900-01-01", "to": "9999-01-01"},
        }
        resp = requests.post(
            AMAC_MANAGER_API + "?page=0&size=10",
            json=payload,
            headers=AMAC_MANAGER_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"  [机构类型API] 查询失败 status={resp.status_code}")
            return None

        data = resp.json()
        total = data.get("totalElements", 0)
        if total == 0 or "content" not in data or len(data["content"]) == 0:
            return None

        for item in data["content"]:
            item_name = item.get("managerName", "")
            if org_name == item_name:
                org_type = item.get("primaryInvestType", "")
                if org_type:
                    logger.info(f"  [机构类型API] {org_name} → {org_type}")
                    return org_type

        for item in data["content"]:
            item_name = item.get("managerName", "")
            if org_name in item_name or item_name in org_name:
                org_type = item.get("primaryInvestType", "")
                if org_type:
                    logger.info(f"  [机构类型API] {org_name} → {org_type} (包含匹配)")
                    return org_type

        return None
    except Exception as e:
        logger.warning(f"  [机构类型API] 查询异常: {e}")
        return None


CANCELLED_API_MAX_RETRIES = 3
CANCELLED_API_RETRY_DELAY = 3


def query_org_type_from_amac_cancelled(org_name: str) -> Optional[str]:
    """从中基协已注销管理人API查询机构类型（注销前登记类型）

    搜索API受WAF保护，脚本调用大部分时候返回400，偶尔能成功。
    采用重试策略：最多尝试 CANCELLED_API_MAX_RETRIES 次，每次间隔 CANCELLED_API_RETRY_DELAY 秒。
    一旦搜索API成功返回结果，后续的详情页请求是稳定的。
    """
    if len(org_name) < 4:
        return None

    generic_suffixes = ["有限公司", "股份有限公司", "投资有限公司", "管理有限公司",
                        "基金管理有限公司", "资本管理有限公司", "资产管理有限公司"]
    if org_name in generic_suffixes:
        return None

    import random as _rand

    for attempt in range(1, CANCELLED_API_MAX_RETRIES + 1):
        try:
            rand_val = _rand.random()
            url = f"{AMAC_CANCELLED_API}?rand={rand_val}&page=0&size=10"
            resp = requests.post(
                url,
                json={"keyword": org_name},
                headers=AMAC_CANCELLED_HEADERS,
                timeout=15,
            )
            if resp.status_code != 200:
                logger.debug(
                    "  [已注销API] 第%d次查询失败 status=%d", attempt, resp.status_code,
                )
                if attempt < CANCELLED_API_MAX_RETRIES:
                    time.sleep(CANCELLED_API_RETRY_DELAY)
                continue

            data = resp.json()
            total = data.get("totalElements", 0)
            if total == 0 or "content" not in data or len(data["content"]) == 0:
                return None

            target_item = None
            for item in data["content"]:
                item_name = item.get("orgName", "")
                clean_name = re.sub(r"<[^>]+>", "", item_name)
                if org_name == clean_name:
                    target_item = item
                    break

            if not target_item:
                for item in data["content"]:
                    item_name = item.get("orgName", "")
                    clean_name = re.sub(r"<[^>]+>", "", item_name)
                    if org_name in clean_name or clean_name in org_name:
                        target_item = item
                        break

            if not target_item:
                return None

            tenant_id = target_item.get("userTenantId", "")
            if not tenant_id:
                return None

            detail_url = f"{AMAC_CANCELLED_DETAIL_URL}/{tenant_id}.html"
            detail_resp = requests.get(
                detail_url,
                headers={"User-Agent": AMAC_CANCELLED_HEADERS["User-Agent"]},
                timeout=15,
            )
            if detail_resp.status_code != 200:
                logger.debug("  [已注销详情] 页面获取失败 status=%d", detail_resp.status_code)
                return None

            detail_resp.encoding = "utf-8"
            m = re.search(r"机构类型.*?<td[^>]*>(.*?)</td>", detail_resp.text, re.S)
            if m:
                org_type = m.group(1).strip()
                if org_type:
                    logger.info("  [已注销API] %s → %s", org_name, org_type)
                    return org_type

            return None
        except Exception as e:
            logger.debug("  [已注销API] 第%d次查询异常: %s", attempt, e)
            if attempt < CANCELLED_API_MAX_RETRIES:
                time.sleep(CANCELLED_API_RETRY_DELAY)

    return None


def extract_org_type_from_text(org_name: str, raw_text: str) -> Optional[str]:
    """从案例正文中正则匹配机构类型（仅当正文恰好包含类型字符串时）"""
    if not raw_text or len(raw_text) < 100:
        return None

    snippet = raw_text[:3000]

    for org_type in ORG_TYPE_VALUES:
        if org_type in snippet:
            logger.info(f"  [机构类型文本] {org_name} → {org_type} (正则匹配)")
            return org_type

    return None


def extract_full_org_name_from_text(short_name: Optional[str], raw_text: str) -> Optional[str]:
    """从正文正则提取机构完整名称（当标题只有简称时使用）"""
    if not raw_text or len(raw_text) < 10:
        return None

    snippet = raw_text[:3000]

    if short_name:
        escaped = re.escape(short_name)
        m = re.search(
            r"([^，,：:；;\n]+)（以下简称" + escaped + r"）",
            snippet,
        )
        if m:
            name = m.group(1).strip()
            for prefix in ["申请人：", "被申请人：", "申请人:", "被申请人:",
                           "当事人：", "当事人:", "被处分机构：", "被处分机构:"]:
                if name.startswith(prefix):
                    name = name[len(prefix):].strip()
            if len(name) >= 4 and re.search(r"(?:公司|企业|有限|合伙|事务所|集团)", name):
                return name

    for prefix in ["申请人", "被申请人", "被处分机构", "当事人"]:
        m = re.search(
            prefix + r"[：:]\s*([^\n,，。；;（(]+?(?:公司|企业|有限|合伙|事务所|集团)[^\n]*?)(?:[，,。\n（(]|$)",
            snippet,
        )
        if m:
            name = m.group(1).strip()
            if len(name) >= 4:
                return name

    for prefix in ["申请人", "被申请人", "被处分机构", "当事人"]:
        m = re.search(
            prefix + r"[：:]\s*([^\n，,。；;]+?(?:有限公司|股份有限公司|企业|合伙|事务所|集团))",
            snippet,
        )
        if m:
            name = m.group(1).strip()
            if len(name) >= 4:
                return name

    m = re.search(
        r"([^，,：:；;\n]+?(?:公司|企业|有限|合伙|事务所|集团)[^，,：:；;\n]*?)（以下简称",
        snippet,
    )
    if m:
        name = m.group(1).strip()
        for prefix in ["申请人：", "被申请人：", "申请人:", "被申请人:",
                       "当事人：", "当事人:", "被处分机构：", "被处分机构:"]:
            if name.startswith(prefix):
                name = name[len(prefix):].strip()
        if len(name) >= 4:
            return name

    return None


def extract_org_name_from_text_llm(title: str, raw_text: str) -> Optional[str]:
    """使用大模型从正文提取受处分机构完整名称（降级方案）。"""
    if not raw_text or len(raw_text) < 100:
        return None

    prompt = (
        "请从以下纪律处分决定书文本中，提取被处分机构的完整注册名称。\n"
        "请只输出机构完整名称，不要输出其他内容。如果文本中没有提及，请输出\"未知\"。\n\n"
        "文本内容：\n" + raw_text[:2000]
    )
    try:
        client = get_llm(task="summarize")
        result = client.chat_text(prompt, max_tokens=100, temperature=0.1).strip()
        result = result.strip("「」《》\"'· \n")
        if result and result != "未知" and len(result) >= 4:
            return result
    except LLMError as exc:
        logger.warning("  [机构名称LLM] 提取失败: %s", exc)
    except Exception as exc:  # noqa: BLE001 - LLM 异常不应中断抓取
        logger.warning("  [机构名称LLM] 提取异常: %s", exc)
    return None


def extract_punished_entity(title: str, raw_text: str, category_key: str) -> str:
    """提取受处分机构的完整名称，三级策略：标题→正则→LLM"""
    if category_key != "Institution":
        return ""

    title_name = extract_org_name_from_title(title)

    if title_name and re.search(r"(?:公司|企业|有限|合伙|事务所|集团)", title_name):
        return title_name

    if raw_text:
        full_name = extract_full_org_name_from_text(title_name, raw_text)
        if full_name:
            logger.info(f"  [机构名称正则] {title_name or '?'} → {full_name}")
            return full_name

    if raw_text and len(raw_text) >= 100:
        full_name = extract_org_name_from_text_llm(title, raw_text)
        if full_name:
            logger.info(f"  [机构名称LLM] {title_name or '?'} → {full_name}")
            return full_name

    return title_name or ""


def resolve_org_type(
    title: str,
    raw_text: str,
    category_key: str,
    cache: Optional[OrgTypeCache] = None,
    punished_entity: str = "",
) -> str:
    """解析机构类型，四级降级：缓存 → 活跃API → 已注销API → 正文正则"""
    if category_key != "Institution":
        return ""

    org_name = punished_entity or extract_org_name_from_title(title)
    if not org_name:
        logger.debug(f"  [机构类型] 无法提取机构名: {title}")
        return ""

    if cache:
        cached = cache.get(org_name)
        if cached:
            logger.info(f"  [机构类型缓存] {org_name} → {cached}")
            return cached

    org_type = query_org_type_from_amac(org_name)

    if not org_type:
        org_type = query_org_type_from_amac_cancelled(org_name)

    if not org_type:
        org_type = extract_org_type_from_text(org_name, raw_text)

    if org_type and cache:
        cache.set(org_name, org_type)

    return org_type or ""


# ──────────────────────────── 历史案例回填 ────────────────────────────


@dataclass
class BackfillResult:
    """一次机构类型回填任务的汇总结果。"""

    total: int = 0
    already_have: int = 0
    org_type_filled: int = 0
    entity_filled: int = 0
    unresolved: int = 0
    manual_list: List[Dict[str, Any]] = field(default_factory=list)
    manual_list_path: str = ""
    dry_run: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "already_have": self.already_have,
            "org_type_filled": self.org_type_filled,
            "entity_filled": self.entity_filled,
            "unresolved": self.unresolved,
            "manual_count": len(self.manual_list),
            "manual_list_path": self.manual_list_path,
            "dry_run": self.dry_run,
        }


def iter_amac_institution_cases(
    config: Optional[Config] = None,
) -> Iterator[Tuple[Path, Dict[str, Any]]]:
    """遍历 AMAC 机构类案例文件，产出 ``(路径, 数据)``。"""
    cases_dir = (config or get_config()).data_root("amac_cases")
    institution_dir = cases_dir / "institution"
    if not institution_dir.exists():
        return
    for path in sorted(institution_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        data = read_json(path)
        if data is not None:
            yield path, data


def extract_org_type_from_url(url: str) -> str:
    """从已注销管理人详情页 URL 中提取机构类型；失败返回空串。"""
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("  [机构类型URL] 请求失败: %s", exc)
        return ""

    if response.status_code != 200:
        logger.warning("  [机构类型URL] 页面获取失败 (status=%s)", response.status_code)
        return ""

    response.encoding = "utf-8"
    match = re.search(r"机构类型.*?<td[^>]*>(.*?)</td>", response.text, re.S)
    if match:
        return match.group(1).strip()
    logger.warning("  [机构类型URL] 页面中未找到「机构类型」字段")
    return ""


def backfill_org_types(
    dry_run: bool = False,
    config: Optional[Config] = None,
    limit: Optional[int] = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> BackfillResult:
    """为历史 AMAC 机构类案例补齐 ``org_type`` 与 ``punished_entity``。

    Args:
        dry_run: 仅统计不写盘。
        config: 配置对象，默认使用全局单例。
        limit: 最多处理多少条（便于抽样验证）。
        on_progress: 进度回调 ``(序号, 总数, 案例ID)``。

    Returns:
        :class:`BackfillResult`；``manual_list`` 为无法自动解析、需人工补全的清单。
    """
    cfg = config or get_config()
    cases_dir = cfg.data_root("amac_cases")
    cache = OrgTypeCache(cases_dir)

    items = list(iter_amac_institution_cases(cfg))
    if limit:
        items = items[:limit]

    result = BackfillResult(total=len(items), dry_run=dry_run)
    logger.info("扫描到 %d 个机构类案例，开始%s",
                result.total, "试算（dry-run）" if dry_run else "回填")

    for index, (path, data) in enumerate(items, 1):
        case_id = str(data.get("case_id", path.stem))
        if on_progress:
            on_progress(index, len(items), case_id)

        if data.get("org_type"):
            result.already_have += 1
            continue

        title = str(data.get("title", ""))
        raw_text = str(data.get("raw_text", ""))
        punished_entity = str(data.get("punished_entity", ""))
        if not punished_entity:
            punished_entity = extract_punished_entity(title, raw_text, "Institution") or ""
            if punished_entity:
                result.entity_filled += 1

        org_type = resolve_org_type(title, raw_text, "Institution", cache, punished_entity)
        entity_changed = bool(punished_entity) and punished_entity != data.get("punished_entity")

        if org_type:
            result.org_type_filled += 1
            logger.info("[%d/%d] %s → %s", index, len(items), case_id, org_type)
            data["org_type"] = org_type
        else:
            result.unresolved += 1
            logger.warning("[%d/%d] %s → 无法自动获取机构类型", index, len(items), case_id)
            result.manual_list.append({
                "case_id": case_id,
                "title": title,
                "punished_entity": punished_entity,
                "file": path.as_posix(),
            })

        if entity_changed:
            data["punished_entity"] = punished_entity

        if not dry_run and (org_type or entity_changed):
            data["backfill_time"] = datetime.now().isoformat(timespec="seconds")
            write_json(path, data)

        time.sleep(1.5)

    if result.manual_list:
        manual_path = cases_dir / "_org_type_manual.json"
        result.manual_list_path = manual_path.as_posix()
        if not dry_run:
            write_json(manual_path, result.manual_list)
        logger.info("需人工补全清单：%s（%d 条）", manual_path, len(result.manual_list))

    logger.info("回填完成：已有 %d，补全机构类型 %d，补全机构名称 %d，未解析 %d",
                result.already_have, result.org_type_filled,
                result.entity_filled, result.unresolved)
    return result


def interactive_fill(
    config: Optional[Config] = None,
) -> Tuple[int, int]:
    """交互式终端补全缺失的机构类型，返回 ``(已补全, 已跳过)``。

    输入规则：编号选择预置类型 | 直接输入自定义类型 | 粘贴详情页 URL 自动提取
    | 回车跳过 | ``q`` 退出。
    """
    cfg = config or get_config()
    cases_dir = cfg.data_root("amac_cases")
    cache = OrgTypeCache(cases_dir)

    missing: List[Tuple[Path, Dict[str, Any]]] = [
        (path, data) for path, data in iter_amac_institution_cases(cfg)
        if not data.get("org_type")
    ]
    if not missing:
        print("\n所有机构类案例均已拥有 org_type，无需补全。")
        return 0, 0

    print(f"\n发现 {len(missing)} 个案例缺少 org_type，开始交互式补全")
    print("  输入编号选择类型 | 直接输入自定义类型 | 粘贴详情页URL自动提取 | 回车跳过 | q 退出\n")
    for index, value in enumerate(ORG_TYPE_VALUES, 1):
        print(f"  {index}. {value}")
    print()

    filled = skipped = 0
    for index, (path, data) in enumerate(missing, 1):
        title = str(data.get("title", ""))
        punished_entity = str(data.get("punished_entity", ""))
        case_id = str(data.get("case_id", path.stem))

        print(f"\n{'-' * 50}")
        print(f"  [{index}/{len(missing)}] 案例: {case_id}")
        print(f"  机构: {punished_entity or '(无机构名称)'}")
        print(f"  标题: {title}")

        while True:
            try:
                choice = input("  机构类型 (编号/自定义/URL/回车跳过/q退出): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\n退出交互模式。")
                print(f"交互式补全结束。已补全 {filled} 条，跳过 {skipped} 条。")
                return filled, skipped

            if choice.lower() == "q":
                print(f"\n交互式补全结束。已补全 {filled} 条，跳过 {skipped} 条。")
                return filled, skipped

            if choice == "":
                skipped += 1
                print("  → 跳过")
                break

            if choice.startswith("http"):
                org_type = extract_org_type_from_url(choice)
                if not org_type:
                    continue
                print(f"  → 从URL提取: {org_type}")
            elif choice.isdigit() and 1 <= int(choice) <= len(ORG_TYPE_VALUES):
                org_type = ORG_TYPE_VALUES[int(choice) - 1]
            else:
                org_type = choice

            data["org_type"] = org_type
            data["backfill_time"] = datetime.now().isoformat(timespec="seconds")
            write_json(path, data)
            if punished_entity:
                cache.set(punished_entity, org_type)
            filled += 1
            print(f"  ✓ 已保存: {org_type}")
            break

    print(f"\n交互式补全结束。已补全 {filled} 条，跳过 {skipped} 条。")
    return filled, skipped


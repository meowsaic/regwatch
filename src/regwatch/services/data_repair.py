"""确定性数据质量修复（不调用模型）。

对已入库案例做可规则推导的回填与清洗：

1. AMAC 标题括号 → ``punished_entity``（含嵌套括号 / 注销公告）
2. CSRC 脏当事人清洗（「采取出具警示函措施」等尾巴）与空值回填
3. CSRC 脏当事人替换（「我局对…」「上述行为违反了…」等正文句子 → 权威主体）
4. CSRC 标题 / 正文 → ``document_number``
5. 正文落款中文日期 → ``summaries.punishment_date``（仅填空）；清空无效日期
6. 从 cases 回填 ``summaries.punished_entity``；修复摘要侧脏当事人
7. 规范 ``entity_type``；重建 ``case_violations``；列出过短正文

全部写操作经仓储层；支持 dry-run。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..db import DataStore
from ..domain import Dataset
from ..logging_setup import get_logger

logger = get_logger("data_repair")

__all__ = [
    "DataRepairService",
    "RepairReport",
    "clean_csrc_entity",
    "extract_authoritative_csrc_entity",
    "extract_document_number",
    "extract_punished_entity_from_title",
    "extract_punishment_date_from_text",
    "has_compound_action_title",
    "is_description_like_amac_entity",
    "is_dirty_csrc_entity",
    "is_valid_date",
    "normalize_entity_type",
]

#: 标题末尾括号内的当事人（全角/半角，允许嵌套一层公司名括号）
_TITLE_BRACKET = re.compile(
    r"[（(]([^（()）]{1,80}(?:[（(][^（()）]{1,40}[）)])?[^（()）]{0,80})[）)]\s*$"
)
_TITLE_BRACKET_ALT = re.compile(r"[〔【\[]([^〕】\]]{1,80})[〕】\]]")

#: 文书号：〔2023〕141 号 / 【2022】2号 / (2021)221号
_DOC_NUMBER = re.compile(r"(?:〔|【|\[|\()(\d{4})(?:〕|】|\]|\))\s*(\d{1,4})\s*号?")
_DOC_NUMBER_PREFIX = re.compile(
    r"((?:中国证券监督管理委员会)?[^〔【\[\（]{0,20}(?:监管局|证监局|证监会)?公告)?"
    r"[〔【\[\（](\d{4})[〕】\]\）]\s*(\d{1,4})\s*号"
)

#: 正文日期：2026年5月11日 / 二〇二六年五月十一日
_DATE_ARABIC = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_CN_DIGIT = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_NUM = re.compile(r"[〇零一二三四五六七八九十]{2,6}")

_STOPWORDS_IN_BRACKET = (
    "决定书",
    "告知书",
    "送达",
    "公告",
    "纪律处分",
    "行政处罚",
    "监管措施",
    "事先告知",
)

#: 明显是从正文句子误抓的当事人特征（含动作词、法规名、叙述开头等）
_DIRTY_ENTITY = re.compile(
    r"我局|经查|上述|违反|发现|存在|涉嫌|以下简称|《|》|依据|"
    r"一是|二是|三是|四是|五是|你在|反映出|决定书|措施|警示函|监管谈话|"
    r"^\d|^一、|公告|通知|内部控制|未按|未及时|未对|未将|未在|未能|未完成"
)
#: 「熊蕾等22人」「邱亚夫等九名人员」「吴正杲等相关责任人」这类标题原文写法，不算脏值
_ENTITY_OK_TAIL = re.compile(r"等[^，,。；;（）()]{0,12}(?:人|人员|主体)$")
_ENTITY_PERSON = re.compile(r"^[\u4e00-\u9fa5·]{2,6}$")
#: 外文名称（含大小写、数字、点号与变音字母，如 UweRondé / BLACKRIVERFOOD2PTE.LTD.）
_ENTITY_LATIN = re.compile(r"^[A-Za-z][^\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]{1,39}$")
#: 股票简称（ST云中药 / *ST东达）
_ENTITY_STOCK = re.compile(r"^\*?ST[\u4e00-\u9fa5A-Za-z]{2,8}$")
_ENTITY_ORG = re.compile(
    r"公司|企业|合伙|中心|事务所|集团|基金|投资|资本|资产|资管|证券|银行|"
    r"期货|保险|信托|控股|股份|协会|基金会|大学|研究院|研究所|商行"
)
#: 标题原文里的补充描述段（「XX及相关责任人员」「XX及注册会计师YY」等），合法
_ENTITY_DESC_PART = re.compile(
    r"^(?:其|及|等|各|在|相关|有关|责任人员|责任人|相关人员|有关人员|注册会计师|"
    r"签字会计师|一致行动人|经办人员|评估师|律师|会计师)"
)
#: 主体词 + 描述词混写（「恩捷股份实际控制人家族」）
_ENTITY_DESC_WORD = re.compile(r"责任|人员|主体|家族|实际控制人|一致行动人|股东|高管|自然人")
#: 机构名结尾（同上，判断首段是否是完整机构名）
_ENTITY_ORG_TAIL = re.compile(
    r"公司|企业|中心|事务所|集团|合伙|协会|基金会|银行|证券|期货|基金|研究所|营业部|分公司"
)
#: AMAC 把公告标题片段误当主体的特征（如「期间届满未提交专项法律意见书」）
_AMAC_DESC_ENTITY = re.compile(r"届满|意见书|公告|通知|办法|规定|决定书|的$")


def _squash(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip()


def is_description_like_amac_entity(value: str) -> bool:
    """AMAC 当事人是否是公告标题片段（非机构 / 人名）。"""
    text = _squash(value)
    if not text or re.search(r"公司|企业|合伙|中心|事务所|集团|基金", text):
        return False
    return bool(_AMAC_DESC_ENTITY.search(text))


def is_dirty_csrc_entity(value: str) -> bool:
    """判断 CSRC 当事人是否为脏值。

    脏值来源：旧版采集/摘要从正文句子里误抓的片段，如
    「我局在对杭州巨鲸财富管理有限公司」「上述行为违反了《…》」。
    判定方式：含叙述/法规特征词，或逐段均不像自然人 / 机构名。
    标题原文的多种合法写法（「XX及相关责任人员」「XX及在湘各营业部」等）
    以**首段**为准放行，避免误报。
    """
    text = _squash(value)
    if not text:
        return False
    if _DIRTY_ENTITY.search(text):
        return True
    if _ENTITY_OK_TAIL.search(text):
        return False
    parts = [part for part in re.split(r"[、,，;；/﹑及]", text) if part.strip()]
    if not parts:
        return False
    first = parts[0].strip()
    if (
        _ENTITY_PERSON.match(first)
        or _ENTITY_LATIN.match(first)
        or _ENTITY_STOCK.match(first)
        or (_ENTITY_ORG.search(first) and _ENTITY_ORG_TAIL.search(first))
    ):
        return False
    for part in parts:
        part = part.strip()
        if (
            _ENTITY_PERSON.match(part)
            or _ENTITY_LATIN.match(part)
            or _ENTITY_STOCK.match(part)
            or _ENTITY_ORG.search(part)
            or _ENTITY_DESC_PART.match(part)
            or (_ENTITY_DESC_WORD.search(part) and _ENTITY_ORG.search(part))
        ):
            continue
        return True
    return False


#: 标题中的「对…采取/出具…」段（复合标题可有多段，如「对A…采取责令改正并对B…出具警示函」）
#: 主体列表允许顿号（「对A公司、B公司、张三采取…」），仅以句读与括号为界
_TITLE_ACTION = re.compile(
    r"对([^，,。；;（()）]+?)(?:采取|出具|作出|给予|责令|实施|处以|撤销|注销|暂停|取消|"
    r"认定为不适当人选|公开谴责|限制股东权利)"
)


def _entities_from_action_title(title: str) -> str:
    """从标题的动作段合并提取主体（支持复合标题的多批主体）。"""
    parts: list[str] = []
    for match in _TITLE_ACTION.finditer(title or ""):
        value = _squash(match.group(1))
        value = re.sub(r"^(?:其|该)", "", value)
        if not (2 <= len(value) <= 160) or value in parts:
            continue
        if is_dirty_csrc_entity(value):
            continue
        parts.append(value)
    return "、".join(parts)


def has_compound_action_title(title: str) -> bool:
    """标题是否含多个动作段（如「对A…采取责令改正并对B…出具警示函」）。"""
    return len(_TITLE_ACTION.findall(title or "")) >= 2


def extract_authoritative_csrc_entity(title: str, body: str) -> str:
    """提取权威受处罚主体（标题 + 正文「当事人」，拒绝正文句子片段）。

    依次尝试标题动作段（含复合标题）→ 采集层链路（标题动作词 → 标题括号 →
    正文「当事人：」）→ 标题专用兜底；返回空串表示无法确定
    （调用方应跳过而非写入猜测值）。
    """
    for candidate in (
        _entities_from_action_title(title or ""),
        extract_csrc_entities(title or "", body or ""),
        extract_punished_entity_from_title(title or ""),
    ):
        value = _squash(candidate)
        if value and 2 <= len(value) <= 160 and not is_dirty_csrc_entity(value):
            return value
    return ""


def _cn_to_int(text: str) -> int | None:
    """把中文数字（≤99 或 年份四字）转 int。"""
    text = text.strip()
    if not text:
        return None
    if all(ch in "〇零一二三四五六七八九" for ch in text):
        value = 0
        for ch in text:
            value = value * 10 + _CN_DIGIT[ch]
        return value
    # 十/十一/二十一/九十九
    if "十" in text:
        parts = text.split("十")
        tens = _CN_DIGIT.get(parts[0], 1) if parts[0] else 1
        ones = _CN_DIGIT.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    return _CN_DIGIT.get(text)


def clean_csrc_entity(raw: str) -> str:
    """清洗 CSRC 当事人中的监管措施尾巴。"""
    text = re.sub(r"\s+", "", (raw or "").strip())
    if not text:
        return ""
    # 多主体时逐段清洗
    parts = re.split(r"[、，,]", text)
    cleaned_parts: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        part = re.split(
            r"(?:采取|出具|作出|给予|责令|实施|处以|撤销|注销|暂停|取消|"
            r"监管谈话|行政处罚|监管措施|行政监管措施|警示函|监管函|"
            r"监管工作函|责令改正)",
            part,
            maxsplit=1,
        )[0]
        part = part.rstrip("的、，,：:；;（(【[")
        if len(part) >= 2:
            cleaned_parts.append(part)
    return "、".join(cleaned_parts)


def is_valid_date(value: str) -> bool:
    """判断 YYYY-MM-DD 是否为合法日历日。"""
    text = (value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    year, month, day = (int(x) for x in text.split("-"))
    if not (2000 <= year <= 2030):
        return False
    if not (1 <= month <= 12):
        return False
    if not (1 <= day <= 31):
        return False
    # 粗校验月份天数
    if month in {4, 6, 9, 11} and day > 30:
        return False
    return not (month == 2 and day > 29)


def normalize_entity_type(raw: str) -> str:
    """把「机构+个人」等写法规范为「机构、个人」；空则推断失败返回空。"""
    text = re.sub(r"\s+", "", (raw or "").strip())
    if not text:
        return ""
    text = text.replace("+", "、").replace("／", "、").replace("/", "、")
    has_org = "机构" in text
    has_person = "个人" in text
    if has_org and has_person:
        return "机构、个人"
    if has_org:
        return "机构"
    if has_person:
        return "个人"
    return text


def extract_entity_from_deregistration_title(title: str) -> str:
    """从「关于注销XXX私募基金管理人登记的公告」提取主体。"""
    text = (title or "").strip()
    match = re.search(r"关于注销(.+?)(?:私募基金)?管理人登记的公告", text)
    if match:
        raw = re.sub(r"\s+", "", match.group(1).strip())
        raw = re.sub(r"私募基金$", "", raw)
        # 通用公告（如「关于注销期间届满未提交专项法律意见书…」）没有单一主体
        if re.search(r"届满|意见书|未提交|未完成|未按|公告|通知|规定|办法|决定|名单", raw):
            return ""
        if 2 <= len(raw) <= 80:
            return raw
    return ""


def extract_punished_entity_from_title(title: str) -> str:
    """从 AMAC / CSRC 标题末尾括号提取当事人。"""
    text = (title or "").strip()
    if not text:
        return ""

    # 注销公告单独处理（不在末尾括号）
    dereg = extract_entity_from_deregistration_title(text)
    if dereg:
        return dereg

    for pattern in (_TITLE_BRACKET, _TITLE_BRACKET_ALT):
        match = pattern.search(text)
        if not match:
            continue
        raw = match.group(1).strip()
        # 「关于对XXX采取…的决定」不在末尾括号，走前缀模式
        if any(token in raw for token in _STOPWORDS_IN_BRACKET):
            continue
        # 文书号 / 纯数字不是当事人（如 〔2022〕19号、2026）
        if re.fullmatch(r"\d{1,4}", raw) or re.search(r"[〔\[【]\d{4}", raw):
            continue
        if re.fullmatch(r"\d{4}〔\d〕\d+号?", raw):
            continue
        # 书名号、空白清理
        raw = raw.replace("《", "").replace("》", "").strip()
        raw = re.sub(r"\s+", "", raw)
        if 2 <= len(raw) <= 80 and not raw.isdigit():
            return raw

    # 嵌套未闭合：标题含「（XXX（YYY）」且以机构后缀结尾
    if re.search(r"(?:公司|中心|合伙|企业)$", text):
        nested = re.search(
            r"[（(]((?:[^（()）]+[（(][^（()）]+[）)])+[^（()）]*)$",
            text,
        )
        if nested:
            raw = nested.group(1).strip().rstrip("（(")
            if raw and not raw.isdigit() and 2 <= len(raw) <= 80:
                return raw

    # CSRC：关于对XXX采取… / 对XXX出具…
    match = re.search(r"(?:关于)?对(.+?)(?:采取|出具|作出|给予|责令)", text)
    if match:
        raw = re.sub(r"\s+", "", match.group(1).strip())
        if raw and raw not in {"其", "当事人"} and 2 <= len(raw) <= 80:
            return clean_csrc_entity(raw)
    # AMAC 复核决定书-姓名（半角连字符）
    match = re.search(r"(?:复核)?决定书\s*[-–—]\s*([一-龥·]{2,20})\s*$", text)
    if match:
        return match.group(1).strip()
    # 未闭合括号：纪律处分决定书（泓泉投资管理（平潭）有限公司
    if text.endswith("公司") or text.endswith("中心") or text.endswith("合伙"):
        match = re.search(r"[（(]([一-龥][^（()）]*(?:[（(][^（()）]*[）)])?[^（()）]*)$", text)
        if match:
            raw = match.group(1).strip().rstrip("（(")
            if raw and not raw.isdigit() and 2 <= len(raw) <= 80:
                return raw
    return ""


def extract_document_number(title: str, body: str = "") -> str:
    """抽取文书号，优先标题中的 〔YYYY〕N号；兼容沪〔2025〕40号 等前缀写法。"""
    for source in (title or "", (body or "")[:2000]):
        if not source:
            continue
        match = _DOC_NUMBER_PREFIX.search(source)
        if match:
            prefix = (match.group(1) or "").strip()
            number = f"〔{match.group(2)}〕{match.group(3)}号"
            return f"{prefix}{number}" if prefix and "公告" in prefix else number
        match = _DOC_NUMBER.search(source)
        if match:
            return f"〔{match.group(1)}〕{match.group(2)}号"
        # 地方局前缀：沪〔2025〕40号 / 京〔2023〕12号
        match = re.search(r"([一-龥]{1,4})[〔［\[](\d{4})[〕］\]](\d{1,4})号", source)
        if match:
            return match.group(0)
    # 回落到采集层已有实现（含年号写法）
    try:
        from ..sources.csrc.models import extract_document_number as csrc_extract

        return csrc_extract((body or "")[:500] or (title or ""))
    except Exception:  # pragma: no cover - 采集层可选依赖
        return ""


def extract_csrc_entities(title: str, body: str) -> str:
    """CSRC 受处罚主体：机构提取 → 正文「当事人：」→ 标题括号。"""
    try:
        from ..sources.csrc.models import extract_punished_entities

        value = extract_punished_entities(title or "", body or "")
    except Exception:  # pragma: no cover
        value = ""
    value = clean_csrc_entity(value or "")
    if value.strip():
        return value.strip()
    # 行政处罚决定书常见写法：当事人：马钰焰，男，……
    match = re.search(
        r"当事人\s*[：:]\s*([一-龥·]{2,10})(?:[，,、\s]|（|\()",
        (body or "")[:1500],
    )
    if match:
        name = match.group(1).strip()
        if name not in {"其", "本人", "上述", "该"}:
            return name
    return extract_punished_entity_from_title(title)


#: 落款日期之后允许剩余的正文字数（「抄送：…」等），超过则视为正文中部日期
_DATE_TAIL_MARGIN = 200
_CN_DATE = re.compile(
    r"([〇零一二三四五六七八九]{4})\s*年\s*([〇零一二三四五六七八九十]{1,3})\s*月"
    r"\s*([〇零一二三四五六七八九十]{1,3})\s*日"
)


def extract_punishment_date_from_text(text: str) -> str:
    """从正文抽取落款处分日期（阿拉伯与中文日期取最靠后者，且须贴近文末）。

    正文中部出现的日期（如「2014年6月4日由高培峰变更为孟宪武」）不是落款，
    因此只接受距文末 :data:`_DATE_TAIL_MARGIN` 以内的日期。
    """
    if not text:
        return ""
    # 取正文后半段更可能是落款
    tail = text[-1500:] if len(text) > 1500 else text
    best_end = -1
    best_value = ""

    for match in _DATE_ARABIC.finditer(tail):
        year, month, day = (int(part) for part in match.groups())
        value = f"{year:04d}-{month:02d}-{day:02d}"
        if not is_valid_date(value) or len(tail) - match.end() > _DATE_TAIL_MARGIN:
            continue
        best_end, best_value = match.end(), value

    # 中文日期：二〇二六年五月十一日
    for match in _CN_DATE.finditer(tail):
        if len(tail) - match.end() > _DATE_TAIL_MARGIN:
            continue
        cn_year = _cn_to_int(match.group(1))
        cn_month = _cn_to_int(match.group(2))
        cn_day = _cn_to_int(match.group(3))
        if not (cn_year and cn_month and cn_day):
            continue
        if not (1 <= cn_month <= 12 and 1 <= cn_day <= 31):
            continue
        if match.end() > best_end:
            best_end = match.end()
            best_value = f"{cn_year:04d}-{cn_month:02d}-{cn_day:02d}"
    return best_value


@dataclass(slots=True)
class RepairReport:
    """一次修复的结果汇总。"""

    dry_run: bool = True
    amac_entity_candidates: int = 0
    amac_entity_filled: int = 0
    amac_entity_cleared: int = 0
    csrc_entity_candidates: int = 0
    csrc_entity_filled: int = 0
    csrc_entity_cleaned: int = 0
    csrc_entity_dirty_replaced: int = 0
    csrc_entity_dirty_unresolved: int = 0
    csrc_docnum_candidates: int = 0
    csrc_docnum_filled: int = 0
    punishment_date_filled: int = 0
    punishment_date_cleared: int = 0
    summary_entity_filled: int = 0
    summary_entity_dirty_replaced: int = 0
    summary_entity_dirty_unresolved: int = 0
    entity_type_normalized: int = 0
    violations_rebuilt: int = 0
    short_bodies: list[dict[str, Any]] = field(default_factory=list)
    samples: dict[str, list[str]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["short_body_count"] = len(self.short_bodies)
        return data


class DataRepairService:
    """确定性回填与体检。"""

    def __init__(self, store: DataStore) -> None:
        self._store = store

    def repair(self, *, dry_run: bool = False, short_body_max: int = 200) -> RepairReport:
        report = RepairReport(dry_run=dry_run)
        self._fill_amac_entities(report, dry_run=dry_run)
        self._clear_descriptive_amac_entities(report, dry_run=dry_run)
        self._fill_csrc_entities(report, dry_run=dry_run)
        self._clean_csrc_entities(report, dry_run=dry_run)
        self._repair_dirty_csrc_entities(report, dry_run=dry_run)
        self._fill_csrc_document_numbers(report, dry_run=dry_run)
        self._fill_punishment_dates(report, dry_run=dry_run)
        self._clear_invalid_punishment_dates(report, dry_run=dry_run)
        self._backfill_summary_entities(report, dry_run=dry_run)
        self._repair_dirty_summary_entities(report, dry_run=dry_run)
        self._normalize_entity_types(report, dry_run=dry_run)
        if not dry_run:
            report.violations_rebuilt = self._store.summaries.rebuild_violations()
        report.short_bodies = [
            {"dataset": ds, "case_id": cid, "length": n}
            for ds, cid, n in self._store.cases.list_short_bodies(max_length=short_body_max)
        ]
        if not dry_run:
            self._store.touch()
        return report

    def _iter_cases(self, dataset: Dataset):
        # 走仓储 search 接口，避免裸 SQL
        from ..domain import CaseQuery

        rows = self._store.cases.search(CaseQuery(datasets=(dataset,), limit=0))
        yield from rows

    def _fill_amac_entities(self, report: RepairReport, *, dry_run: bool) -> None:
        samples: list[str] = []
        for row in self._iter_cases(Dataset.AMAC):
            current = (row.punished_entities or "").strip()
            entity = extract_punished_entity_from_title(row.title)
            if not entity:
                continue
            # 已是正确值则跳过；注销公告脏值 / 被截断的后缀则强制覆盖
            is_dirty = current.startswith("关于注销")
            is_truncated = bool(current) and entity != current and current in entity
            if current and not is_dirty and not is_truncated:
                continue
            report.amac_entity_candidates += 1
            if len(samples) < 8:
                samples.append(f"{row.case_id}: {current or '∅'} → {entity}")
            if not dry_run:
                if self._store.cases.set_punished_entity(
                    Dataset.AMAC,
                    row.case_id,
                    entity,
                    only_if_empty=not (is_dirty or is_truncated),
                ):
                    report.amac_entity_filled += 1
            else:
                report.amac_entity_filled += 1
        report.samples["amac_entity"] = samples

    def _fill_csrc_entities(self, report: RepairReport, *, dry_run: bool) -> None:
        samples: list[str] = []
        for row in self._iter_cases(Dataset.CSRC):
            if (row.punished_entities or "").strip():
                continue
            body = self._store.cases.get_body(Dataset.CSRC, row.case_id)
            entity = extract_csrc_entities(row.title, body)
            if not entity or len(entity.strip()) < 2:
                continue
            report.csrc_entity_candidates += 1
            if len(samples) < 8:
                samples.append(f"{row.case_id}: {entity}")
            if not dry_run:
                if self._store.cases.set_punished_entities(
                    Dataset.CSRC, row.case_id, entity.strip(), only_if_empty=True
                ):
                    report.csrc_entity_filled += 1
            else:
                report.csrc_entity_filled += 1
        report.samples["csrc_entity"] = samples

    def _clear_descriptive_amac_entities(self, report: RepairReport, *, dry_run: bool) -> None:
        """清空 AMAC 把公告标题片段误当主体的值（案例侧与摘要侧同源同步清）。"""
        samples: list[str] = []
        for row in self._iter_cases(Dataset.AMAC):
            # CaseRow.punished_entities 是「cases.punished_entities → 摘要 → cases.punished_entity」的合并值
            current = (row.punished_entities or "").strip()
            if not current or not is_description_like_amac_entity(current):
                continue
            report.amac_entity_cleared += 1
            if len(samples) < 8:
                samples.append(f"{row.case_id}: {current} → ∅")
            if not dry_run:
                self._store.cases.set_punished_entity(
                    Dataset.AMAC, row.case_id, "", only_if_empty=False
                )
                summary = self._store.summaries.get(Dataset.AMAC, row.case_id)
                if summary is not None and summary.punished_entity.strip() == current:
                    self._store.summaries.set_punished_entity(
                        Dataset.AMAC, row.case_id, "", only_if_empty=False
                    )
        report.samples["amac_entity_cleared"] = samples

    def _clean_csrc_entities(self, report: RepairReport, *, dry_run: bool) -> None:
        """清洗已入库的脏当事人（措施尾巴）。"""
        samples: list[str] = []
        for row in self._iter_cases(Dataset.CSRC):
            current = (row.punished_entities or "").strip()
            if not current:
                continue
            cleaned = clean_csrc_entity(current)
            if not cleaned or cleaned == current:
                continue
            report.csrc_entity_cleaned += 1
            if len(samples) < 8:
                samples.append(f"{row.case_id}: {current} → {cleaned}")
            if not dry_run:
                self._store.cases.set_punished_entities(
                    Dataset.CSRC, row.case_id, cleaned, only_if_empty=False
                )
        report.samples["csrc_entity_cleaned"] = samples

    def _repair_dirty_csrc_entities(self, report: RepairReport, *, dry_run: bool) -> None:
        """把从正文句子误抓的脏当事人替换为权威主体（标题 / 正文「当事人」）。

        仅覆盖判定为脏的值；无法确定权威值时保留原值并计入 unresolved，
        避免用新的猜测覆盖旧值。
        """
        samples: list[str] = []
        unresolved: list[str] = []
        for row in self._iter_cases(Dataset.CSRC):
            current = (row.punished_entities or "").strip()
            if not current:
                continue
            is_compound = has_compound_action_title(row.title)
            if not is_dirty_csrc_entity(current) and not is_compound:
                continue
            body = self._store.cases.get_body(Dataset.CSRC, row.case_id)
            entity = extract_authoritative_csrc_entity(row.title, body)
            if not entity or is_dirty_csrc_entity(entity):
                report.csrc_entity_dirty_unresolved += 1
                if len(unresolved) < 12:
                    unresolved.append(f"{row.case_id}: {current}")
                continue
            if entity == current:
                continue
            if is_compound and not is_dirty_csrc_entity(current) and len(entity) < len(current):
                # 复合标题但新提取更短（如标题用简称、现值是正文全称）：保留更完整的现值
                continue
            report.csrc_entity_dirty_replaced += 1
            if len(samples) < 12:
                samples.append(f"{row.case_id}: {current} → {entity}")
            if not dry_run:
                self._store.cases.set_punished_entities(
                    Dataset.CSRC, row.case_id, entity, only_if_empty=False
                )
        report.samples["csrc_entity_dirty"] = samples
        report.samples["csrc_entity_dirty_unresolved"] = unresolved

    def _repair_dirty_summary_entities(self, report: RepairReport, *, dry_run: bool) -> None:
        """修复摘要侧的脏当事人（优先用案例侧已修复值，其次重新提取）。"""
        samples: list[str] = []
        unresolved: list[str] = []
        for item in self._store.summaries.list_with_punished_entity(Dataset.CSRC):
            current = (item["punished_entity"] or "").strip()
            if not current or not is_dirty_csrc_entity(current):
                continue
            case_id = str(item["case_id"])
            target = (item.get("punished_entities") or "").strip()
            if not target or is_dirty_csrc_entity(target):
                body = self._store.cases.get_body(Dataset.CSRC, case_id)
                target = extract_authoritative_csrc_entity(str(item.get("title") or ""), body)
            if not target or target == current or is_dirty_csrc_entity(target):
                report.summary_entity_dirty_unresolved += 1
                if len(unresolved) < 12:
                    unresolved.append(f"{case_id}: {current}")
                continue
            report.summary_entity_dirty_replaced += 1
            if len(samples) < 12:
                samples.append(f"{case_id}: {current} → {target}")
            if not dry_run:
                self._store.summaries.set_punished_entity(
                    Dataset.CSRC, case_id, target, only_if_empty=False
                )
        report.samples["summary_entity_dirty"] = samples
        report.samples["summary_entity_dirty_unresolved"] = unresolved

    def _fill_csrc_document_numbers(self, report: RepairReport, *, dry_run: bool) -> None:
        samples: list[str] = []
        for row in self._iter_cases(Dataset.CSRC):
            if (row.document_number or "").strip():
                continue
            body = self._store.cases.get_body(Dataset.CSRC, row.case_id)
            number = extract_document_number(row.title, body)
            if not number:
                continue
            report.csrc_docnum_candidates += 1
            if len(samples) < 8:
                samples.append(f"{row.case_id}: {number}")
            if not dry_run:
                if self._store.cases.set_document_number(
                    Dataset.CSRC, row.case_id, number, only_if_empty=True
                ):
                    report.csrc_docnum_filled += 1
            else:
                report.csrc_docnum_filled += 1
        report.samples["csrc_docnum"] = samples

    def _fill_punishment_dates(self, report: RepairReport, *, dry_run: bool) -> None:
        samples: list[str] = []
        from ..domain import CaseQuery, CaseStatus

        rows = self._store.cases.search(CaseQuery(statuses=(CaseStatus.DONE,), limit=0))
        for row in rows:
            if (row.punishment_date or "").strip():
                continue
            if "正文与标题不符" in (row.status_note or ""):
                # 正文已被网站更新覆盖：从中提取的日期必错，跳过
                continue
            body = self._store.cases.get_body(row.dataset, row.case_id)
            date_text = extract_punishment_date_from_text(body)
            if not date_text or not is_valid_date(date_text):
                continue
            if len(samples) < 8:
                samples.append(f"{row.dataset}:{row.case_id} → {date_text}")
            if not dry_run:
                if self._store.summaries.set_punishment_date(
                    row.dataset, row.case_id, date_text, only_if_empty=True
                ):
                    report.punishment_date_filled += 1
            else:
                report.punishment_date_filled += 1
        report.samples["punishment_date"] = samples

    def _clear_invalid_punishment_dates(self, report: RepairReport, *, dry_run: bool) -> None:
        """清空 OCR 产生的非法处分日（如 2024-08-74、1996-02-01）。"""
        from ..domain import CaseQuery, CaseStatus

        samples: list[str] = []
        rows = self._store.cases.search(CaseQuery(statuses=(CaseStatus.DONE,), limit=0))
        for row in rows:
            current = (row.punishment_date or "").strip()
            if not current or is_valid_date(current):
                continue
            report.punishment_date_cleared += 1
            if len(samples) < 12:
                samples.append(f"{row.dataset}:{row.case_id} 清除 {current}")
            if not dry_run:
                self._store.summaries.set_punishment_date(
                    row.dataset, row.case_id, "", only_if_empty=False
                )
        report.samples["punishment_date_cleared"] = samples

    def _backfill_summary_entities(self, report: RepairReport, *, dry_run: bool) -> None:
        """从 cases 回填空的 summaries.punished_entity。"""
        samples: list[str] = []
        rows = self._store.summaries.list_for_field_fill(field="punished_entity")
        for item in rows:
            source = (item.get("punished_entity") or item.get("punished_entities") or "").strip()
            if not source:
                continue
            source = clean_csrc_entity(source) if item["dataset"] == Dataset.CSRC.value else source
            if not source:
                continue
            report.summary_entity_filled += 1
            if len(samples) < 8:
                samples.append(f"{item['dataset']}:{item['case_id']} ← {source}")
            if not dry_run:
                self._store.summaries.set_punished_entity(
                    item["dataset"], item["case_id"], source, only_if_empty=True
                )
        report.samples["summary_entity"] = samples

    def _normalize_entity_types(self, report: RepairReport, *, dry_run: bool) -> None:
        """规范 entity_type 写法（机构+个人 → 机构、个人）。"""
        from ..domain import CaseQuery, CaseStatus

        samples: list[str] = []
        rows = self._store.cases.search(CaseQuery(statuses=(CaseStatus.DONE,), limit=0))
        for row in rows:
            current = (row.entity_type or "").strip()
            if not current:
                continue
            normalized = normalize_entity_type(current)
            if not normalized or normalized == current:
                continue
            report.entity_type_normalized += 1
            if len(samples) < 8:
                samples.append(f"{row.dataset}:{row.case_id} {current} → {normalized}")
            if not dry_run:
                self._store.summaries.set_entity_type(
                    row.dataset, row.case_id, normalized, only_if_empty=False
                )
        report.samples["entity_type"] = samples

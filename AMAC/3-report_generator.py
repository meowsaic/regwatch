"""
AMAC纪律处分案例报告生成器 (report_generator.py)
功能：读取 case_summarizer 产出的结构化摘要JSON，进行统计分析和聚合，
     生成季度分析报告（Markdown格式），支持纯统计报告和LLM增强报告两种模式。
"""

import os
import json
import time
import logging
import sys
import traceback
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass
from collections import Counter

PROVIDER = "glm"

PROVIDER_CONFIG = {
    "glm": {
        "api_key_env": "ZHIPU_API_KEY",
        "api_key_default": "",
        "vision_model": "glm-4.6v-flash",
        "text_model": "glm-4.7-flash",
    },
    "mimo": {
        "api_key_env": "MIMO_API_KEY",
        "api_key_default": "",
        "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
        "vision_model": "MiMo-V2.5",
        "text_model": "mimo-v2.5",
    },
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key_default": "",
        "base_url": "https://api.deepseek.com",
        "text_model": "deepseek-v4-flash",
    },
    "zen": {
        "api_key_env": "OPENCODE_ZEN_API_KEY",
        "api_key_default": "",
        "base_url": "https://opencode.ai/zen/v1",
        "vision_model": "mimo-v2.5-free",
        "text_model": "x-preview-f-free",
        "text_models": {
            "x-preview-f-free": "Ox Alpha Free",
            "hy3-free": "Hy3 Free",
            "deepseek-v4-flash": "DeepSeek V4 Flash",
        },
    },
}

VIOLATION_TYPES = [
    "违规募集", "未按规定备案", "登记信息失实",
    "违规投资运作", "挪用基金财产", "违规关联交易",
    "未按规定托管", "未按规定估值",
    "非专业化运营", "未尽勤勉尽责义务",
    "内控缺失", "人员与场所违规", "未持续符合登记条件",
    "信息披露违规", "未配合自律管理", "其他",
]

VIOLATION_ADVICE = {
    "违规募集": [
        "严格审查募集渠道资质，禁止委托无基金销售资格机构开展募集",
        "杜绝任何形式的保本保收益承诺，包括口头承诺和抽屉协议",
        "完善投资者适当性管理，确保风险评级与投资者风险承受能力匹配",
    ],
    "未按规定备案": [
        "建立基金产品备案台账，新设基金及时向协会办理备案手续",
        "指定专人跟踪备案进度，避免因人员变动导致备案遗漏",
        "定期核对已管理产品与已备案产品清单，确保无遗漏",
    ],
    "登记信息失实": [
        "确保登记备案信息真实、准确、完整，杜绝虚假填报",
        "重大事项变更时及时向协会提交变更申请",
        "定期核对从业人员管理系统与实际人员情况，确保一致",
    ],
    "违规投资运作": [
        "严格遵守基金合同约定的投资范围和投资限制",
        "建立投资决策审批流程，重大投资需经合规审查",
        "禁止开展资金池业务和通道业务，确保每只基金独立运作",
    ],
    "挪用基金财产": [
        "严格隔离基金财产与管理人自有财产",
        "禁止将基金财产用于担保、明股实债等非约定用途",
        "建立资金划拨双人复核机制，防范资金挪用风险",
    ],
    "违规关联交易": [
        "建立关联交易管理制度，关联交易需经合规审查和披露",
        "防范利益输送，确保关联交易价格公允",
    ],
    "未按规定托管": [
        "按照规定为私募基金选取合格托管机构",
        "确保基金资产由托管机构独立保管，避免资金混同",
    ],
    "未按规定估值": [
        "按照合同约定和行业规范制定估值方法",
        "定期由独立第三方进行估值核对",
    ],
    "非专业化运营": [
        "主营业务应清晰聚焦于私募基金管理，不得兼营无关业务",
        "严禁从事民间借贷、担保、保理、小额贷款等与私募基金管理无关的业务",
        "定期审查公司经营范围和实际业务，确保不存在利益冲突",
    ],
    "未尽勤勉尽责义务": [
        "切实履行谨慎勤勉义务，对投资标的进行充分尽职调查",
        "核实投资者与投资标的之间的关联关系，防范利益冲突",
        "主动管理基金财产，不得疏于管理或放任不管",
    ],
    "内控缺失": [
        "建立健全内部控制体系，确保合规风控人员独立履职",
        "禁止合规风控人员兼任投资等冲突职务",
        "完善档案管理制度，妥善保管募集、投资等业务资料",
    ],
    "人员与场所违规": [
        "确保办公场所独立，不得与关联方共用办公场地",
        "配备足够数量的专职人员，满足管理人最低人员要求",
        "高管任职须符合资格条件，禁止合规风控负责人兼任冲突职务",
    ],
    "未持续符合登记条件": [
        "定期自查管理人登记条件持续符合情况，包括人员、场所、资本金等",
        "发生重大变更时及时向协会报告并更新登记信息",
        "对不再符合条件的情况及时整改，避免被撤销登记",
    ],
    "信息披露违规": [
        "建立信息披露日历，按合同约定和监管要求定期披露",
        "确保投资者信息获取渠道畅通，及时更新联系方式",
        "披露内容做到真实、准确、完整，避免选择性披露",
    ],
    "未配合自律管理": [
        "积极配合协会自律检查，如实提供相关材料",
        "对协会提出的整改要求在规定期限内完成整改",
        "建立与监管机构的常态化沟通机制",
    ],
    "其他": [
        "加强从业人员合规培训，定期组织法规学习",
        "建立合规自查机制，及时发现和整改问题",
    ],
}

PUNISHMENT_CATEGORIES = {
    "警告": ["警告"],
    "公开谴责": ["公开谴责"],
    "暂停受理备案": ["暂停受理"],
    "撤销管理人登记": ["撤销"],
    "取消会员资格": ["取消会员"],
    "加入黑名单": ["黑名单"],
    "其他": [],
}


# ──────────────────────────── 日志 ────────────────────────────

def setup_logging() -> logging.Logger:
    logger = logging.getLogger("report_generator")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    return logger


logger = setup_logging()


# ──────────────────────────── 数据加载 ────────────────────────────

def load_summaries(summaries_dir: Path) -> List[Dict]:
    if not summaries_dir.exists():
        logger.warning(f"摘要目录不存在: {summaries_dir}")
        return []

    summaries = []
    for json_path in sorted(summaries_dir.rglob("*_summary.json")):
        if json_path.name.startswith("_"):
            continue
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("extract_success", False):
                summaries.append(data)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"读取摘要文件失败 {json_path.name}: {e}")

    summaries.sort(key=lambda x: x.get("date", ""))
    logger.info(f"加载到 {len(summaries)} 个成功提取的案例摘要")
    return summaries


def filter_by_date_range(
    summaries: List[Dict],
    start_date: str,
    end_date: str,
) -> List[Dict]:
    filtered = []
    for s in summaries:
        d = s.get("date", "")
        if d and start_date <= d <= end_date:
            filtered.append(s)
    return filtered


# ──────────────────────────── 统计分析 ────────────────────────────

def split_multi_value(text: str, separators=None) -> List[str]:
    if not text:
        return []
    if separators is None:
        separators = ["；", ";", "、", ","]
    for sep in separators:
        text = text.replace(sep, "|||")
    parts = [p.strip() for p in text.split("|||") if p.strip()]
    return parts


def compute_basic_stats(summaries: List[Dict]) -> Dict:
    total = len(summaries)
    institutions = [s for s in summaries if s.get("entity_type") == "机构"]
    personnel = [s for s in summaries if s.get("entity_type") == "个人"]

    dates = [s.get("date", "") for s in summaries if s.get("date")]
    date_range = ""
    if dates:
        date_range = f"{min(dates)} ~ {max(dates)}"

    return {
        "total": total,
        "institution_count": len(institutions),
        "personnel_count": len(personnel),
        "date_range": date_range,
        "summaries": summaries,
        "institutions": institutions,
        "personnel": personnel,
    }


def compute_violation_stats(summaries: List[Dict]) -> Dict:
    counter = Counter()
    case_map = {}

    for s in summaries:
        types = split_multi_value(s.get("violation_type", ""))
        if not types:
            types = ["未分类"]
        for vt in types:
            matched = None
            for known in VIOLATION_TYPES:
                if known in vt:
                    matched = known
                    break
            if matched:
                counter[matched] += 1
                case_map.setdefault(matched, []).append(s)
            else:
                counter[vt] += 1
                case_map.setdefault(vt, []).append(s)

    total_mentions = sum(counter.values())
    ranked = counter.most_common()

    return {
        "counter": counter,
        "ranked": ranked,
        "total_mentions": total_mentions,
        "case_map": case_map,
    }


def compute_punishment_stats(summaries: List[Dict]) -> Dict:
    counter = Counter()
    category_counter = Counter()
    case_map = {}

    for s in summaries:
        punishment = s.get("punishment", "").strip()
        if not punishment:
            counter["未明确"] += 1
            case_map.setdefault("未明确", []).append(s)
            continue

        counter[punishment] += 1
        case_map.setdefault(punishment, []).append(s)

        categorized = False
        for cat_name, keywords in PUNISHMENT_CATEGORIES.items():
            if cat_name == "其他":
                continue
            for kw in keywords:
                if kw in punishment:
                    category_counter[cat_name] += 1
                    categorized = True
                    break
            if categorized:
                break
        if not categorized:
            category_counter["其他"] += 1

    ranked = counter.most_common()
    category_ranked = category_counter.most_common()

    return {
        "counter": counter,
        "ranked": ranked,
        "category_counter": category_counter,
        "category_ranked": category_ranked,
        "case_map": case_map,
    }


def compute_legal_basis_stats(summaries: List[Dict]) -> Dict:
    counter = Counter()

    for s in summaries:
        bases = split_multi_value(s.get("legal_basis", ""))
        for b in bases:
            b_clean = b.strip()
            if not b_clean:
                continue
            m = re.search(r"《([^》]+)》", b_clean)
            if m:
                law_name = m.group(1)
                if re.match(r"^第[一二三四五六七八九十百千]+条", law_name):
                    continue
                counter[law_name] += 1
            else:
                if re.match(r"^第[一二三四五六七八九十百千]+条", b_clean):
                    continue
                counter[b_clean] += 1

    ranked = counter.most_common(20)
    return {
        "counter": counter,
        "ranked": ranked,
    }


def compute_entity_comparison(institutions: List[Dict], personnel: List[Dict]) -> Dict:
    inst_violations = Counter()
    for s in institutions:
        for vt in split_multi_value(s.get("violation_type", "")):
            matched = None
            for known in VIOLATION_TYPES:
                if known in vt:
                    matched = known
                    break
            inst_violations[matched or vt] += 1

    pers_violations = Counter()
    for s in personnel:
        for vt in split_multi_value(s.get("violation_type", "")):
            matched = None
            for known in VIOLATION_TYPES:
                if known in vt:
                    matched = known
                    break
            pers_violations[matched or vt] += 1

    inst_punishments = Counter()
    for s in institutions:
        p = s.get("punishment", "").strip()
        if p:
            inst_punishments[p] += 1

    pers_punishments = Counter()
    for s in personnel:
        p = s.get("punishment", "").strip()
        if p:
            pers_punishments[p] += 1

    return {
        "inst_violations": inst_violations.most_common(10),
        "pers_violations": pers_violations.most_common(10),
        "inst_punishments": inst_punishments.most_common(5),
        "pers_punishments": pers_punishments.most_common(5),
    }


def pick_representative_cases(violation_case_map: Dict, max_per_type: int = 2) -> Dict:
    result = {}
    for vtype, cases in violation_case_map.items():
        sorted_cases = sorted(cases, key=lambda x: len(x.get("violation_summary", "")), reverse=True)
        result[vtype] = sorted_cases[:max_per_type]
    return result


# ──────────────────────────── Markdown 渲染 ────────────────────────────

def render_overview(stats: Dict, quarter_label: str) -> str:
    lines = [
        f"## 一、季度概况",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 报告周期 | {quarter_label} |",
        f"| 案例日期范围 | {stats['date_range']} |",
        f"| 处分案例总数 | **{stats['total']}** |",
        f"| 受处分机构 | {stats['institution_count']} |",
        f"| 受处分人员 | {stats['personnel_count']} |",
        "",
    ]

    inst_ratio = stats['institution_count'] / stats['total'] * 100 if stats['total'] else 0
    pers_ratio = stats['personnel_count'] / stats['total'] * 100 if stats['total'] else 0
    lines.append(f"本季度中基协共发布 **{stats['total']}** 例纪律处分，其中机构 {stats['institution_count']} 例（{inst_ratio:.1f}%），人员 {stats['personnel_count']} 例（{pers_ratio:.1f}%）。")
    lines.append("")
    return "\n".join(lines)


def render_violation_stats(vstats: Dict, total_cases: int) -> str:
    lines = [
        "## 二、违规类型统计",
        "",
        "### 2.1 违规类型分布",
        "",
        "| 排名 | 违规类型 | 涉及案例数 | 占比 |",
        "|------|----------|------------|------|",
    ]

    for rank, (vtype, count) in enumerate(vstats["ranked"], 1):
        pct = count / total_cases * 100 if total_cases else 0
        bar = "█" * int(pct / 2) + "░" * (25 - int(pct / 2))
        lines.append(f"| {rank} | {vtype} | {count} | {pct:.1f}% |")

    lines.append("")
    lines.append(f"> 共涉及 **{len(vstats['ranked'])}** 种违规类型，**{vstats['total_mentions']}** 次违规提及（部分案例涉及多种违规）。")
    lines.append("")

    if vstats["ranked"]:
        top = vstats["ranked"][0]
        lines.append(f"最突出的违规类型为 **{top[0]}**，涉及 {top[1]} 例，占全部案例的 {top[1] / total_cases * 100:.1f}%。")
    lines.append("")
    return "\n".join(lines)


def render_punishment_stats(pstats: Dict, total_cases: int) -> str:
    lines = [
        "## 三、处罚措施分析",
        "",
        "### 3.1 处罚类别分布",
        "",
        "| 处罚类别 | 案例数 | 占比 |",
        "|----------|--------|------|",
    ]

    for cat, count in pstats["category_ranked"]:
        pct = count / total_cases * 100 if total_cases else 0
        lines.append(f"| {cat} | {count} | {pct:.1f}% |")

    lines.append("")

    lines.append("### 3.2 具体处罚措施")
    lines.append("")
    lines.append("| 处罚措施 | 案例数 |")
    lines.append("|----------|--------|")

    for punishment, count in pstats["ranked"][:15]:
        lines.append(f"| {punishment} | {count} |")

    if len(pstats["ranked"]) > 15:
        lines.append(f"| ... (共 {len(pstats['ranked'])} 种) | |")

    lines.append("")
    return "\n".join(lines)


def render_entity_comparison(comparison: Dict, inst_count: int, pers_count: int) -> str:
    lines = [
        "## 四、机构 vs 个人对比",
        "",
        "### 4.1 违规类型对比",
        "",
        "| 违规类型 | 机构 | 人员 |",
        "|----------|------|------|",
    ]

    all_types = set()
    for vt, _ in comparison["inst_violations"]:
        all_types.add(vt)
    for vt, _ in comparison["pers_violations"]:
        all_types.add(vt)

    inst_dict = dict(comparison["inst_violations"])
    pers_dict = dict(comparison["pers_violations"])

    for vt in sorted(all_types, key=lambda x: inst_dict.get(x, 0) + pers_dict.get(x, 0), reverse=True):
        lines.append(f"| {vt} | {inst_dict.get(vt, 0)} | {pers_dict.get(vt, 0)} |")

    lines.append("")

    lines.append("### 4.2 处罚措施对比")
    lines.append("")
    lines.append("**机构处罚 TOP5：**")
    lines.append("")
    for punishment, count in comparison["inst_punishments"]:
        lines.append(f"- {punishment}（{count}例）")
    lines.append("")

    lines.append("**人员处罚 TOP5：**")
    lines.append("")
    for punishment, count in comparison["pers_punishments"]:
        lines.append(f"- {punishment}（{count}例）")
    lines.append("")

    return "\n".join(lines)


def render_legal_basis(lstats: Dict) -> str:
    lines = [
        "## 五、法规依据分析",
        "",
        "| 排名 | 法规名称 | 引用次数 |",
        "|------|----------|----------|",
    ]

    for rank, (law, count) in enumerate(lstats["ranked"], 1):
        lines.append(f"| {rank} | 《{law}》 | {count} |")

    lines.append("")

    if lstats["ranked"]:
        top_law = lstats["ranked"][0]
        lines.append(f"最常被引用的法规为 **《{top_law[0]}》**，共被引用 {top_law[1]} 次。")
    lines.append("")
    return "\n".join(lines)


def render_typical_cases(representative: Dict) -> str:
    lines = [
        "## 六、典型案例",
        "",
    ]

    for vtype in VIOLATION_TYPES:
        cases = representative.get(vtype, [])
        if not cases:
            continue
        lines.append(f"### {vtype}")
        lines.append("")
        for case in cases:
            entity = case.get("punished_entity", "未知")
            etype = case.get("entity_type", "")
            date = case.get("date", "")
            summary = case.get("violation_summary", "无摘要")
            punishment = case.get("punishment", "")
            lines.append(f"**{entity}**（{etype}，{date}）")
            lines.append(f"- 违规事实：{summary}")
            lines.append(f"- 处罚措施：{punishment}")
            lines.append("")

    return "\n".join(lines)


def render_case_list(summaries: List[Dict]) -> str:
    lines = [
        "## 附件：案例列表",
        "",
        "| 序号 | 受处分对象 | 类型 | 违规类型 | 处罚措施 | 日期 |",
        "|------|-----------|------|----------|----------|------|",
    ]

    for i, s in enumerate(summaries, 1):
        entity = s.get("punished_entity", "未知")
        etype = s.get("entity_type", "")
        vtype = s.get("violation_type", "")
        punishment = s.get("punishment", "")
        date = s.get("date", "")
        lines.append(f"| {i} | {entity} | {etype} | {vtype} | {punishment} | {date} |")

    lines.append("")
    return "\n".join(lines)


# ──────────────────────────── LLM 增强 ────────────────────────────

_client_cache = {}


def get_client(provider=None):
    if provider is None:
        provider = PROVIDER
    if provider in _client_cache:
        return _client_cache[provider]

    config = PROVIDER_CONFIG[provider]
    api_key = os.environ.get(config["api_key_env"], config.get("api_key_default", ""))
    if not api_key:
        api_key = input(f"未设置环境变量 {config['api_key_env']}，请输入 API Key: ").strip()
        if api_key:
            os.environ[config["api_key_env"]] = api_key
        else:
            raise ValueError(f"未提供 {config['api_key_env']}，无法初始化客户端")
    client = None

    if provider == "mimo":
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=config["base_url"])
        logger.info(f"MiMo客户端已初始化，模型: {config['text_model']}")
    elif provider == "deepseek":
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=config["base_url"])
        logger.info(f"DeepSeek客户端已初始化，模型: {config['text_model']}")
    elif provider == "zen":
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=config["base_url"])
        logger.info(f"OpenCode Zen客户端已初始化，模型: {config['text_model']}")
    else:
        try:
            from zai import ZhipuAiClient
            client = ZhipuAiClient(api_key=api_key)
            logger.info(f"智谱AI客户端已初始化 (zai.ZhipuAiClient)")
        except ImportError:
            pass
        if client is None:
            try:
                from zhipuai import ZhipuAI
                client = ZhipuAI(api_key=api_key)
                logger.info(f"智谱AI客户端已初始化 (zhipuai.ZhipuAI)")
            except ImportError:
                pass
        if client is None:
            raise ImportError("无法导入智谱AI客户端库，请安装: pip install zai-sdk 或 pip install zhipuai")

    _client_cache[provider] = client
    return client


def llm_chat(messages, model=None, max_tokens=2000, temperature=0.1,
             thinking=None, provider=None, stream=False):
    if provider is None:
        provider = PROVIDER
    if model is None:
        model = PROVIDER_CONFIG[provider]["text_model"]

    client = get_client(provider)

    if provider == "mimo":
        kwargs = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 0.95,
            "stream": stream,
        }
    elif provider == "deepseek":
        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        }
    elif provider == "zen":
        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
    else:
        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if thinking:
            kwargs["thinking"] = thinking

    return client.chat.completions.create(**kwargs)


COMPLIANCE_PROMPT = """你是一名专业的私募基金合规顾问。基于以下AMAC纪律处分的统计数据，请撰写"合规建议"章节。

统计数据摘要：
- 本季度处分案例总数：{total_cases}
- 受处分机构：{inst_count}，受处分人员：{pers_count}
- 违规类型分布：{violation_dist}
- 高频处罚措施：{punishment_dist}
- 最常引用法规：{legal_top3}

请从以下维度给出合规建议（800-1200字）：
1. 针对最突出违规类型的防控建议
2. 内控体系建设重点
3. 信息披露合规要点
4. 人员管理与培训建议

请用专业但易懂的语言，结合数据给出具体可操作的建议。不要使用markdown标题，直接写正文段落。"""


def generate_compliance_advice(all_stats: Dict) -> Optional[str]:
    violation_dist = "、".join([f"{vt}({cnt}例)" for vt, cnt in all_stats["violation"]["ranked"][:8]])
    punishment_dist = "、".join([f"{p}({cnt}例)" for p, cnt in all_stats["punishment"]["category_ranked"][:5]])
    legal_top3 = "、".join([f"《{law}》({cnt}次)" for law, cnt in all_stats["legal"]["ranked"][:3]])

    prompt = COMPLIANCE_PROMPT.format(
        total_cases=all_stats["basic"]["total"],
        inst_count=all_stats["basic"]["institution_count"],
        pers_count=all_stats["basic"]["personnel_count"],
        violation_dist=violation_dist,
        punishment_dist=punishment_dist,
        legal_top3=legal_top3,
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"正在调用LLM生成合规建议... (尝试 {attempt + 1}/{max_retries})")
            response = llm_chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0.7,
                thinking={"type": "disabled"} if PROVIDER == "glm" else None,
            )

            if not hasattr(response, "choices") or len(response.choices) == 0:
                logger.warning("LLM响应结构异常")
                continue

            content = response.choices[0].message.content
            if not content:
                rc = getattr(response.choices[0].message, "reasoning_content", None)
                if rc and len(rc) > 100:
                    content = rc
                else:
                    logger.warning("LLM返回空内容")
                    continue

            logger.info("合规建议生成完成")
            return content.strip()

        except Exception as e:
            logger.warning(f"LLM第 {attempt + 1} 次调用失败: {e}")
            if attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                logger.info(f"{wait}秒后重试...")
                time.sleep(wait)

    logger.error("LLM生成合规建议失败，已达最大重试次数")
    return None


def render_compliance_advice(advice: str) -> str:
    lines = [
        "## 七、合规建议",
        "",
        advice,
        "",
    ]
    return "\n".join(lines)


def render_default_compliance(all_stats: Dict) -> str:
    lines = [
        "## 七、合规建议",
        "",
    ]

    top_violations = all_stats["violation"]["ranked"][:5]
    if top_violations:
        lines.append("### 针对性防控建议")
        lines.append("")
        for vt, cnt in top_violations:
            lines.append(f"**{vt}**（{cnt}例）：")
            advice_list = VIOLATION_ADVICE.get(vt, VIOLATION_ADVICE.get("其他", []))
            for advice in advice_list:
                lines.append(f"- {advice}")
            lines.append("")

    lines.append("### 通用合规建议")
    lines.append("")
    lines.append("1. **完善内控制度**：建立健全内部控制体系，确保各项业务操作符合监管要求")
    lines.append("2. **加强信息披露**：严格按照规定及时、准确、完整地披露基金信息")
    lines.append("3. **规范募集行为**：严禁承诺保本保收益，确保投资者适当性管理到位")
    lines.append("4. **强化人员管理**：加强从业人员合规培训，明确岗位职责和任职要求")
    lines.append("5. **定期合规自查**：建立定期合规检查机制，及时发现和整改问题")
    lines.append("")

    return "\n".join(lines)


# ──────────────────────────── 报告生成 ────────────────────────────

def get_last_quarter_range() -> Tuple[str, str]:
    today = datetime.now()
    y, q = today.year, (today.month - 1) // 3 + 1
    if q == 1:
        ly, lq = y - 1, 4
    else:
        ly, lq = y, q - 1
    start_m = (lq - 1) * 3 + 1
    start = datetime(ly, start_m, 1).date()
    if lq == 4:
        end = datetime(ly + 1, 1, 1).date() - timedelta(days=1)
    else:
        end = datetime(ly, start_m + 3, 1).date() - timedelta(days=1)
    return start.isoformat(), end.isoformat(), ly, lq


def generate_report(
    summaries: List[Dict],
    quarter_label: str,
    use_llm: bool = False,
) -> str:
    if not summaries:
        return "# AMAC纪律处分季度分析报告\n\n暂无案例数据。\n"

    basic = compute_basic_stats(summaries)
    violation = compute_violation_stats(summaries)
    punishment = compute_punishment_stats(summaries)
    legal = compute_legal_basis_stats(summaries)
    comparison = compute_entity_comparison(basic["institutions"], basic["personnel"])
    representative = pick_representative_cases(violation["case_map"])

    all_stats = {
        "basic": basic,
        "violation": violation,
        "punishment": punishment,
        "legal": legal,
        "comparison": comparison,
    }

    sections = []

    header = f"""# AMAC私募基金纪律处分季度分析报告

> 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}
> 报告周期：{quarter_label}
> 数据来源：中国证券投资基金业协会（AMAC）
> 案例数量：{basic['total']}例

---

"""
    sections.append(header)
    sections.append(render_overview(basic, quarter_label))
    sections.append(render_violation_stats(violation, basic["total"]))
    sections.append(render_punishment_stats(punishment, basic["total"]))
    sections.append(render_entity_comparison(comparison, basic["institution_count"], basic["personnel_count"]))
    sections.append(render_legal_basis(legal))
    sections.append(render_typical_cases(representative))

    if use_llm:
        advice = generate_compliance_advice(all_stats)
        if advice:
            sections.append(render_compliance_advice(advice))
        else:
            logger.info("LLM生成失败，使用默认合规建议")
            sections.append(render_default_compliance(all_stats))
    else:
        sections.append(render_default_compliance(all_stats))

    sections.append(render_case_list(summaries))

    footer = f"""

---

*本报告由 report_generator.py 自动生成，数据基于 case_fetcher + case_summarizer 产出的结构化摘要。*
"""
    sections.append(footer)

    return "\n".join(sections)


def save_report(content: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"报告已保存: {output_path}")
    return output_path


def save_stats_json(summaries: List[Dict], output_path: Path):
    if not summaries:
        return

    basic = compute_basic_stats(summaries)
    violation = compute_violation_stats(summaries)
    punishment = compute_punishment_stats(summaries)
    legal = compute_legal_basis_stats(summaries)

    stats_data = {
        "generated_at": datetime.now().isoformat(),
        "basic": {
            "total": basic["total"],
            "institution_count": basic["institution_count"],
            "personnel_count": basic["personnel_count"],
            "date_range": basic["date_range"],
        },
        "violation_distribution": [
            {"type": vt, "count": cnt} for vt, cnt in violation["ranked"]
        ],
        "punishment_categories": [
            {"category": cat, "count": cnt} for cat, cnt in punishment["category_ranked"]
        ],
        "legal_basis_top": [
            {"law": law, "count": cnt} for law, cnt in legal["ranked"][:10]
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(stats_data, f, ensure_ascii=False, indent=2)
    logger.info(f"统计数据已保存: {output_path}")


def _zen_pick_text_model():
    """选择 OpenCode Zen 文本模型（Ox Alpha Free / Hy3 Free / DeepSeek V4 Flash）"""
    tm_cfg = PROVIDER_CONFIG["zen"]
    options = list(tm_cfg["text_models"].items())
    print("\n请选择OpenCode Zen文本模型:")
    for i, (mid, label) in enumerate(options, 1):
        print(f"  {i}. {label}")
    choice = input(f"请输入选择 (1-{len(options)}, 默认1): ").strip() or "1"
    try:
        idx = max(0, min(len(options) - 1, int(choice) - 1))
    except ValueError:
        idx = 0
    tm_cfg["text_model"] = options[idx][0]
    logger.info(f"已选择文本模型: {options[idx][1]} ({options[idx][0]})")


# ──────────────────────────── 主函数 ────────────────────────────

def main():
    global PROVIDER

    print("AMAC纪律处分案例报告生成器 v1.1")
    print("=" * 60)

    print("\n请选择模型提供者:")
    print("1. 智谱AI (GLM)")
    print("2. 小米 (MiMo)")
    print("3. DeepSeek (v4Flash)")
    print("4. OpenCode Zen (免费模型)")
    provider_choice = input("请输入选择 (1-4, 默认1): ").strip() or "1"
    if provider_choice == "2":
        PROVIDER = "mimo"
        logger.info("已选择MiMo模型提供者")
    elif provider_choice == "3":
        PROVIDER = "deepseek"
        logger.info("已选择DeepSeek模型提供者")
    elif provider_choice == "4":
        PROVIDER = "zen"
        logger.info("已选择OpenCode Zen模型提供者")
        _zen_pick_text_model()
    else:
        logger.info("已选择智谱AI模型提供者")

    start_time = time.time()

    script_dir = Path(__file__).parent.resolve()
    summaries_dir = script_dir / "summaries"
    reports_dir = script_dir / "reports"

    print("\n请选择报告模式:")
    print("1. 上一季度报告（自动计算日期范围）")
    print("2. 指定日期范围报告")
    print("3. 全量报告（不按日期过滤）")

    choice = input("请输入选择 (1-3, 默认1): ").strip() or "1"

    try:
        if choice == "1":
            start_str, end_str, ly, lq = get_last_quarter_range()
            quarter_label = f"{ly}年Q{lq}（{start_str} ~ {end_str}）"
            logger.info(f"目标季度: {quarter_label}")

        elif choice == "2":
            start_str = input("起始日期 (YYYY-MM-DD): ").strip()
            end_str = input("结束日期 (YYYY-MM-DD): ").strip()
            quarter_label = f"{start_str} ~ {end_str}"

        elif choice == "3":
            start_str = ""
            end_str = ""
            quarter_label = "全量数据"
        else:
            print("无效选择，退出。")
            return

        use_llm_input = input("是否使用LLM生成合规建议？(y/N): ").strip().lower()
        use_llm = use_llm_input in ("y", "yes")

        summaries = load_summaries(summaries_dir)
        if not summaries:
            print("未找到任何摘要数据，请先运行 case_summarizer.py")
            return

        if start_str and end_str:
            filtered = filter_by_date_range(summaries, start_str, end_str)
            logger.info(f"日期过滤后: {len(filtered)} / {len(summaries)} 个案例")
            if not filtered:
                print(f"指定日期范围内无案例数据")
                return
            summaries = filtered

        report_content = generate_report(summaries, quarter_label, use_llm=use_llm)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        report_filename = f"AMAC_季度分析报告_{timestamp}.md"
        report_path = reports_dir / report_filename
        save_report(report_content, report_path)

        stats_filename = f"stats_{timestamp}.json"
        stats_path = reports_dir / stats_filename
        save_stats_json(summaries, stats_path)

        elapsed = time.time() - start_time
        print(f"\n{'='*60}")
        print(f"报告生成完成")
        print(f"  报告文件: {report_path}")
        print(f"  统计数据: {stats_path}")
        print(f"  耗时: {elapsed:.1f}秒")
        print(f"{'='*60}")

    except KeyboardInterrupt:
        logger.info("\n用户中断，正在退出...")
    except Exception as e:
        logger.error(f"程序异常: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()

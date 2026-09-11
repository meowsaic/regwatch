"""报告渲染层。

沿用历史季度报告的章节结构，保证新旧报告可比：

1. 概况 → 2. 违规类型统计 → 3. 处罚措施分析 → 4. 机构 vs 个人对比
5. 法规依据分析 → 6. 典型案例 → 7. 合规建议 → 附件：案例列表

输出三种格式：

- Markdown（``.md``，主格式）
- HTML（``.html``，深蓝琥珀配色、可直接在浏览器查看）
- JSON（``stats_*.json``，供二次分析与图表复用）

「合规建议」章节可选用大模型撰写（``task="report"`` 绑定的模型），
失败时自动回落到内置的针对性防控建议。
"""

from __future__ import annotations

import html as _html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .analyze import AnalysisResult, analyze
from .config import Config, get_config
from .llm import LLMError, get_llm
from .logutil import get_logger
from .prompts import COMPLIANCE_ADVICE_PROMPT, VIOLATION_ADVICE
from .storage import (
    DATASET_AMAC,
    DATASET_LABELS,
    filter_rows,
)

__all__ = [
    "ReportOutput",
    "render_markdown",
    "render_html",
    "markdown_to_html",
    "generate_compliance_advice",
    "default_compliance_advice",
    "build_report",
    "save_report_files",
    "get_last_quarter_label",
]

logger = get_logger("report")

#: 每个违规类型展示的代表案例数
MAX_CASES_PER_TYPE = 2

#: 案例清单附件最多展示的行数
MAX_CASE_LIST_ROWS = 300


# ──────────────────────────── Markdown 渲染 ────────────────────────────


def _pct(part: int, whole: int) -> float:
    return part / whole * 100 if whole else 0.0


def _render_overview(result: AnalysisResult, period_label: str, dataset_label: str) -> str:
    basic = result.basic
    total = basic["total"]
    inst = basic["institution_count"]
    pers = basic["personnel_count"]
    unknown = basic.get("unknown_entity_count", 0)
    lines = [
        "## 一、概况",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 报告周期 | {period_label} |",
        f"| 数据来源 | {dataset_label} |",
        f"| 案例日期范围 | {basic['date_range'] or '—'} |",
        f"| 处分案例总数 | **{total}** |",
        f"| 受处分机构 | {inst}（{_pct(inst, total):.1f}%） |",
        f"| 受处分人员 | {pers}（{_pct(pers, total):.1f}%） |",
    ]
    if unknown:
        lines.append(f"| 主体类型未标注 | {unknown} |")

    skipped = basic.get("status_counts", {}).get("skipped", 0)
    if skipped:
        lines.append(f"| 判定为非基金相关而跳过 | {skipped} |")

    lines += [
        "",
        f"本期共收录 **{total}** 例处分案例，其中机构 {inst} 例（{_pct(inst, total):.1f}%）、"
        f"人员 {pers} 例（{_pct(pers, total):.1f}%）。",
        "",
    ]
    return "\n".join(lines)


def _render_violation(result: AnalysisResult) -> str:
    violation = result.violation
    total = result.basic["total"]
    lines = [
        "## 二、违规类型统计",
        "",
        "### 2.1 违规类型分布",
        "",
        "| 排名 | 违规类型 | 涉及案例数 | 占比 |",
        "|------|----------|------------|------|",
    ]
    for rank, (vtype, count) in enumerate(violation["ranked"], 1):
        lines.append(f"| {rank} | {vtype} | {count} | {_pct(count, total):.1f}% |")

    lines += [
        "",
        f"> 共涉及 **{violation['type_count']}** 种违规类型，"
        f"**{violation['total_mentions']}** 次违规提及（部分案例涉及多种违规）。",
        "",
    ]
    if violation["ranked"]:
        top_name, top_count = violation["ranked"][0]
        lines.append(
            f"最突出的违规类型为 **{top_name}**，涉及 {top_count} 例，"
            f"占全部案例的 {_pct(top_count, total):.1f}%。"
        )
    lines.append("")
    return "\n".join(lines)


def _render_punishment(result: AnalysisResult) -> str:
    punishment = result.punishment
    total = result.basic["total"]
    lines = [
        "## 三、处罚措施分析",
        "",
        "### 3.1 处罚类别分布",
        "",
        "| 处罚类别 | 案例数 | 占比 |",
        "|----------|--------|------|",
    ]
    for category, count in punishment["category_ranked"]:
        lines.append(f"| {category} | {count} | {_pct(count, total):.1f}% |")

    lines += [
        "",
        "### 3.2 具体处罚措施（TOP 15）",
        "",
        "| 处罚措施 | 案例数 |",
        "|----------|--------|",
    ]
    for name, count in punishment["ranked"][:15]:
        lines.append(f"| {name} | {count} |")
    if len(punishment["ranked"]) > 15:
        lines.append(f"| ……其余 {len(punishment['ranked']) - 15} 种 | |")

    lines.append("")
    return "\n".join(lines)


def _render_comparison(result: AnalysisResult) -> str:
    comparison = result.stats["comparison"]
    lines = [
        "## 四、机构 vs 个人对比",
        "",
        "### 4.1 违规类型对比",
        "",
        "| 违规类型 | 机构 | 人员 |",
        "|----------|------|------|",
    ]
    inst = dict(comparison["inst_violations"])
    pers = dict(comparison["pers_violations"])
    all_types = sorted(
        set(inst) | set(pers),
        key=lambda name: inst.get(name, 0) + pers.get(name, 0),
        reverse=True,
    )
    for vtype in all_types:
        lines.append(f"| {vtype} | {inst.get(vtype, 0)} | {pers.get(vtype, 0)} |")

    lines += ["", "### 4.2 处罚措施对比", "", "**机构处罚 TOP5：**", ""]
    for name, count in comparison["inst_punishments"].most_common():
        lines.append(f"- {name}（{count} 例）")
    lines += ["", "**人员处罚 TOP5：**", ""]
    for name, count in comparison["pers_punishments"].most_common():
        lines.append(f"- {name}（{count} 例）")
    lines.append("")
    return "\n".join(lines)


def _render_legal(result: AnalysisResult) -> str:
    lines = ["## 五、法规依据分析", "", "| 排名 | 法规名称 | 引用次数 |", "|------|----------|----------|"]
    for rank, (law, count) in enumerate(result.legal["ranked"], 1):
        lines.append(f"| {rank} | 《{law}》 | {count} |")
    lines.append("")
    if result.legal["ranked"]:
        top_law, top_count = result.legal["ranked"][0]
        lines.append(f"最常被引用的法规为 **《{top_law}》**，共被引用 {top_count} 次。")
    lines.append("")
    return "\n".join(lines)


def _render_typical_cases(result: AnalysisResult) -> str:
    representative = result.stats["representative"]
    lines = ["## 六、典型案例", ""]
    written = 0
    for cases in representative.values():
        for case in cases:
            lines += [
                f"**{case.entity_display}**（{case.entity_type or '未标注'}，{case.date or '日期未知'}）",
                f"- 违规事实：{case.violation_summary or '（无摘要）'}",
                f"- 处罚措施：{case.punishment or '（未明确）'}",
                "",
            ]
            written += 1
            if written >= 40:
                break
        if written >= 40:
            lines.append("> 代表案例过多，此处仅展示前 40 条。")
            lines.append("")
            break
    if not written:
        lines.append("本期暂无可展示的代表性案例。")
        lines.append("")
    return "\n".join(lines)


def _render_compliance(advice: str) -> str:
    return "\n".join(["## 七、合规建议", "", advice, ""])


def _render_default_compliance(result: AnalysisResult) -> str:
    lines = ["## 七、合规建议", "", "### 针对性防控建议", ""]
    for vtype, count in result.violation["ranked"][:5]:
        lines.append(f"**{vtype}**（{count} 例）：")
        for item in VIOLATION_ADVICE.get(vtype, VIOLATION_ADVICE["其他"]):
            lines.append(f"- {item}")
        lines.append("")

    lines += [
        "### 通用合规建议",
        "",
        "1. **完善内控制度**：建立健全内部控制体系，确保各项业务操作符合监管要求",
        "2. **加强信息披露**：严格按照规定及时、准确、完整地披露基金信息",
        "3. **规范募集行为**：严禁承诺保本保收益，确保投资者适当性管理到位",
        "4. **强化人员管理**：加强从业人员合规培训，明确岗位职责和任职要求",
        "5. **定期合规自查**：建立定期合规检查机制，及时发现和整改问题",
        "",
    ]
    return "\n".join(lines)


def _render_case_list(result: AnalysisResult) -> str:
    lines = ["## 附件：案例列表", "", "| 序号 | 受处分对象 | 类型 | 违规类型 | 处罚措施 | 日期 |",
             "|------|-----------|------|----------|----------|------|"]
    rows = result.rows[:MAX_CASE_LIST_ROWS]
    for index, row in enumerate(rows, 1):
        lines.append(
            f"| {index} | {row.entity_display} | {row.entity_type or '—'} | "
            f"{row.violation_type or '—'} | {row.punishment or '—'} | {row.date or '—'} |"
        )
    if len(result.rows) > MAX_CASE_LIST_ROWS:
        lines.append(f"| …… | 共 {len(result.rows)} 条，仅展示前 {MAX_CASE_LIST_ROWS} 条 | | | | |")
    lines.append("")
    return "\n".join(lines)


def render_markdown(
    result: AnalysisResult,
    title: str,
    period_label: str,
    dataset_label: str,
    advice: Optional[str] = None,
) -> str:
    """把分析结果渲染为完整 Markdown 报告。"""
    header = (
        f"# {title}\n\n"
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}  \n"
        f"> 报告周期：{period_label}  \n"
        f"> 数据来源：{dataset_label}  \n"
        f"> 案例数量：{result.basic['total']} 例\n\n---\n\n"
    )
    sections = [
        header,
        _render_overview(result, period_label, dataset_label),
        _render_violation(result),
        _render_punishment(result),
        _render_comparison(result),
        _render_legal(result),
        _render_typical_cases(result),
        _render_compliance(advice) if advice else _render_default_compliance(result),
        _render_case_list(result),
        "\n---\n\n*本报告由 regwatch 自动生成（regwatch.analyze + regwatch.report）。*\n",
    ]
    return "\n".join(sections)


# ──────────────────────────── HTML 渲染 ────────────────────────────

_HTML_CSS = """
:root{--primary:#354e92;--accent:#2563EB;--amber:#F59E0B;--bg:#F6F8FC;--card:#FFFFFF;
--text:#0F172A;--muted:#475569;--line:#E2E8F0;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font-family:"Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif;font-size:14px;line-height:1.75}
.wrap{max-width:1080px;margin:0 auto;padding:40px 24px 80px}
header.report{background:linear-gradient(135deg,#354e92 0%,#2563EB 60%,#0EA5E9 100%);
color:#fff;border-radius:18px;padding:36px 40px;margin-bottom:28px;box-shadow:0 12px 32px rgba(37,99,235,.18)}
header.report h1{margin:0 0 10px;font-size:30px;font-weight:700;letter-spacing:.5px}
header.report .meta{font-size:13px;opacity:.92}
header.report .meta span{margin-right:18px}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:26px 30px;margin-bottom:22px;box-shadow:0 2px 10px rgba(15,23,42,.04)}
h2{font-size:18px;font-weight:600;margin:0 0 16px;padding-left:12px;
border-left:4px solid var(--amber);color:var(--primary)}
h3{font-size:15px;font-weight:600;margin:20px 0 10px;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0 6px}
th{background:#EEF2F9;color:var(--primary);text-align:left;font-weight:600}
th,td{padding:8px 12px;border-bottom:1px solid var(--line)}
tr:hover td{background:#F8FAFF}
blockquote{margin:12px 0;padding:12px 16px;background:#FFFBEB;border-left:4px solid var(--amber);
color:var(--muted);border-radius:0 8px 8px 0}
ul{margin:8px 0;padding-left:22px}
li{margin:4px 0}
strong{color:var(--primary)}
p{margin:8px 0}
hr{border:0;border-top:1px solid var(--line);margin:26px 0}
footer{color:#94A3B8;font-size:12px;text-align:center;margin-top:30px}
@media print{body{background:#fff}.wrap{padding:0}section{box-shadow:none}}
"""


def _inline(text: str) -> str:
    """行内 Markdown → HTML（加粗 / 斜体 / 行内代码）。"""
    text = _html.escape(text, quote=False)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def markdown_to_html(markdown_text: str, title: str = "", meta: str = "") -> str:
    """把本报告使用的 Markdown 子集渲染为带样式的 HTML。

    支持：标题（# ~ ####）、表格、无序列表、引用、分隔线与段落。
    不追求完整 Markdown 兼容，只覆盖报告实际用到的语法。
    """
    blocks: List[str] = []
    table_rows: List[List[str]] = []
    list_items: List[str] = []
    paragraph: List[str] = []

    def flush_table() -> None:
        if not table_rows:
            return
        header = table_rows[0]
        body = table_rows[1:]
        # 第二行是 |---|---| 分隔行时跳过
        if body and all(re.fullmatch(r":?-{2,}:?", cell.strip()) for cell in body[0]):
            body = body[1:]
        cells = "".join(f"<th>{_inline(cell)}</th>" for cell in header)
        rows = "".join(
            "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>"
            for row in body
        )
        blocks.append(f"<table><thead><tr>{cells}</tr></thead><tbody>{rows}</tbody></table>")
        table_rows.clear()

    def flush_list() -> None:
        if list_items:
            blocks.append("<ul>" + "".join(f"<li>{_inline(item)}</li>" for item in list_items) + "</ul>")
            list_items.clear()

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{'<br>'.join(_inline(line) for line in paragraph)}</p>")
            paragraph.clear()

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            flush_list()
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            table_rows.append(cells)
            continue
        flush_table()

        if not stripped:
            flush_paragraph()
            flush_list()
            continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = min(4, len(heading.group(1)))
            blocks.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue

        if stripped in ("---", "***", "___"):
            flush_paragraph()
            flush_list()
            blocks.append("<hr>")
            continue

        if stripped.startswith("> "):
            flush_paragraph()
            flush_list()
            blocks.append(f"<blockquote>{_inline(stripped[2:])}</blockquote>")
            continue

        if stripped.startswith(("- ", "* ", "• ")):
            flush_paragraph()
            list_items.append(stripped[2:])
            continue

        flush_list()
        paragraph.append(stripped)

    flush_table()
    flush_list()
    flush_paragraph()

    head = f"<title>{_html.escape(title)}</title>" if title else "<title>regwatch 报告</title>"
    header_html = (
        f'<header class="report"><h1>{_html.escape(title)}</h1>'
        f'<div class="meta">{_html.escape(meta)}</div></header>' if title else ""
    )
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"{head}<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<style>{_HTML_CSS}</style></head><body><div class=\"wrap\">"
        f"{header_html}{''.join(blocks)}"
        "<footer>本报告由 regwatch 自动生成</footer>"
        "</div></body></html>"
    )


def render_html(markdown_text: str, title: str, period_label: str, dataset_label: str) -> str:
    """按报告元信息生成 HTML。"""
    meta = f"报告周期：{period_label}　·　数据来源：{dataset_label}　·　生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    return markdown_to_html(markdown_text, title=title, meta=meta)


# ──────────────────────────── 合规建议 ────────────────────────────


def default_compliance_advice(result: AnalysisResult) -> str:
    """内置的合规建议（无需模型）。"""
    return _render_default_compliance(result).split("\n\n", 1)[-1].strip()


def generate_compliance_advice(
    result: AnalysisResult,
    config: Optional[Config] = None,
) -> Optional[str]:
    """调用大模型撰写「合规建议」章节；失败返回 ``None``。"""
    basic = result.basic
    prompt = COMPLIANCE_ADVICE_PROMPT.format(
        total_cases=basic["total"],
        inst_count=basic["institution_count"],
        pers_count=basic["personnel_count"],
        violation_dist="、".join(f"{name}({count}例)" for name, count in result.violation["ranked"][:8]),
        punishment_dist="、".join(f"{name}({count}例)" for name, count in result.punishment["category_ranked"][:5]),
        legal_top3="、".join(f"《{law}》({count}次)" for law, count in result.legal["ranked"][:3]),
        top_violations="、".join(name for name, _ in result.violation["ranked"][:3]) or "无",
    )

    for attempt in range(1, 4):
        try:
            logger.info("正在调用大模型生成合规建议... (%d/3)", attempt)
            client = get_llm(task="report", config=config)
            advice = client.chat_text(
                prompt, max_tokens=2500, temperature=0.7,
            ).strip()
            if len(advice) >= 100:
                logger.info("合规建议生成完成（%d 字）", len(advice))
                return advice
            logger.warning("合规建议过短（%d 字），视为失败", len(advice))
        except LLMError as exc:
            logger.warning("合规建议第 %d 次生成失败: %s", attempt, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("合规建议第 %d 次异常: %s", attempt, exc)

    logger.error("合规建议生成失败，将回落到内置建议")
    return None


# ──────────────────────────── 报告编排 ────────────────────────────


@dataclass
class ReportOutput:
    """一份报告的完整产物。"""

    dataset: str = ""
    title: str = ""
    period_label: str = ""
    markdown: str = ""
    html: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)
    paths: Dict[str, str] = field(default_factory=dict)
    row_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset": self.dataset,
            "title": self.title,
            "period_label": self.period_label,
            "row_count": self.row_count,
            "paths": self.paths,
            "stats": self.stats,
        }


def get_last_quarter_label(today: Optional[datetime] = None) -> str:
    """返回上一个季度的展示标签，如 ``2026年Q1（2026-01-01 ~ 2026-03-31）``。"""
    now = today or datetime.now()
    year, quarter = now.year, (now.month - 1) // 3 + 1
    if quarter == 1:
        last_year, last_quarter = year - 1, 4
    else:
        last_year, last_quarter = year, quarter - 1
    start_month = (last_quarter - 1) * 3 + 1
    start = datetime(last_year, start_month, 1).date()
    if last_quarter == 4:
        end = datetime(last_year + 1, 1, 1).date()
    else:
        end = datetime(last_year, start_month + 3, 1).date()
    end = end.fromordinal(end.toordinal() - 1)
    return f"{last_year}年Q{last_quarter}（{start.isoformat()} ~ {end.isoformat()}）"


def report_title(dataset: str) -> str:
    name = "AMAC私募基金纪律处分" if dataset == DATASET_AMAC else "证监会基金相关处罚与监管措施"
    return f"{name}分析报告"


def save_report_files(
    output: ReportOutput,
    directory: Path,
    stem: Optional[str] = None,
    formats: Sequence[str] = ("md", "html", "json"),
) -> Dict[str, str]:
    """把报告写入磁盘，返回 ``{格式: 路径}``。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stem = stem or f"{output.dataset}_报告_{datetime.now().strftime('%Y%m%d_%H%M')}"
    written: Dict[str, str] = {}

    if "md" in formats:
        path = directory / f"{stem}.md"
        path.write_text(output.markdown, encoding="utf-8")
        written["md"] = str(path)
    if "html" in formats:
        path = directory / f"{stem}.html"
        path.write_text(output.html, encoding="utf-8")
        written["html"] = str(path)
    if "json" in formats:
        path = directory / f"stats_{stem}.json"
        path.write_text(
            json.dumps(output.stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written["json"] = str(path)

    output.paths = written
    for kind, path in written.items():
        logger.info("报告已保存 [%s]: %s", kind, path)
    return written


def build_report(
    dataset: str,
    config: Optional[Config] = None,
    start_date: str = "",
    end_date: str = "",
    use_llm: bool = False,
    period_label: Optional[str] = None,
    title: Optional[str] = None,
) -> ReportOutput:
    """对指定数据集与日期范围生成报告（不落盘）。

    Args:
        dataset: ``amac`` 或 ``csrc``。
        config: 配置对象，默认使用全局单例。
        start_date / end_date: 日期范围（``YYYY-MM-DD``），均为空表示全量数据。
        use_llm: 是否用大模型撰写合规建议。
        period_label: 周期展示文本；默认根据日期范围自动生成。
        title: 报告标题；默认按数据集生成。
    """
    from .storage import build_catalog, dataset_overview  # 局部导入避免循环依赖

    cfg = config or get_config()
    rows = build_catalog(dataset, cfg)
    if start_date or end_date:
        rows = filter_rows(rows, date_from=start_date, date_to=end_date)

    result = analyze(rows)
    if not rows:
        logger.warning("数据集 %s 在 %s ~ %s 范围内没有案例", dataset, start_date, end_date)

    if period_label is None:
        if start_date and end_date:
            period_label = f"{start_date} ~ {end_date}"
        else:
            overview = dataset_overview(dataset, cfg)
            period_label = (
                f"{overview.date_min} ~ {overview.date_max}"
                if overview.date_min and overview.date_max else "全量数据"
            )

    dataset_label = DATASET_LABELS.get(dataset, dataset)
    resolved_title = title or report_title(dataset)

    advice = generate_compliance_advice(result, cfg) if use_llm else None
    markdown = render_markdown(result, resolved_title, period_label, dataset_label, advice)

    return ReportOutput(
        dataset=dataset,
        title=resolved_title,
        period_label=period_label,
        markdown=markdown,
        html=render_html(markdown, resolved_title, period_label, dataset_label),
        stats=result.to_json_payload(),
        row_count=len(rows),
    )

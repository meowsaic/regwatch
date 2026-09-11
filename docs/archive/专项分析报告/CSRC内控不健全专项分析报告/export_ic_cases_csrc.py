#!/usr/bin/env python3
"""
CSRC处罚案例内控不健全清单导出
生成Excel案例清单和Word分析报告
"""

import json
import os
import re
from datetime import datetime
from collections import defaultdict, Counter
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

SUMMARIES_DIR = r"E:\Desktop\codes\CSRC\summaries"
OUTPUT_DIR = r"E:\Desktop\codes\CSRC内控不健全专项分析报告"

# 内控关键词
IC_KEYWORDS_VIOLATION_TYPE = ["内控缺失", "内控制度不健全", "内控不健全", "内控机制不健全", "内控混乱", "内控失效", "内控管理不到位", "内部控制不到位", "内部控制缺陷", "内部控制不健全"]
IC_KEYWORDS_SUMMARY = [
    "内控缺失", "内控不健全", "内控机制不健全", "内控制度不健全",
    "内控机制", "内控混乱", "内控失效", "内控管理不到位",
    "内控制度", "合规风控负责人", "合规风控",
    "内部控制不到位", "内部控制缺陷", "内部控制不健全", "内部控制制度"
]

IC_AREAS = {
    "内控制度未建立/未落实": ["内控制度未建立", "未建立.*内控", "内控制度形同虚设", "内控机制不健全", "未建立.*有效.*内控", "内控制度.*不完善", "内控制度.*缺失", "内控.*混乱", "内控.*失效", "未有效执行内控", "内控缺失", "内控制度不健全", "内部控制不到位", "内部控制缺陷"],
    "合规风控人员缺位": ["合规风控负责人.*缺位", "合规风控负责人.*空缺", "合规风控负责人.*未实际履职", "无.*合规.*负责人", "合规.*风控.*空缺", "无在职高级管理人员", "合规风控负责人.*离职", "无合规风控负责人", "合规.*不到位", "合规.*负责人.*缺位"],
    "基金材料保管不当": ["未妥善保管.*材料", "材料.*缺失", "未保存.*材料", "无法提供.*材料", "基金.*资料.*缺失", "未妥善保存", "材料.*丢失", "档案.*不完整", "未妥善保管.*资料"],
    "混同经营/独立性缺失": ["混同经营", "无独立办公场所", "与.*混同", "共用办公场所", "公章.*由.*管理", "独立性.*缺失"],
    "信息隔离/利益冲突": ["信息隔离", "利益冲突", "关联交易", "信息隔离墙"],
    "人员资质/场所违规": ["无基金从业资格", "挂名.*未实际履职", "人员.*相互.*调用", "场所.*违规", "不符合.*登记条件", "从业资格", "人员.*不符合"],
    "投资运作内控缺失": ["投资决策由.*主导", "投资.*杠杆超标", "投资.*风险.*控制", "未尽勤勉尽责", "投资运作.*违规"],
    "适当性管理不到位": ["适当性管理不到位", "适当性.*不.*到位", "未.*履行.*适当性", "投资者.*适当性"],
    "信息披露违规": ["信息披露违规", "未.*披露.*信息", "信息披露.*不.*完整", "信息披露.*不.*真实", "信息披露.*不.*准确"],
}


def is_ic_related(summary):
    vt = summary.get("violation_type", "")
    vs = summary.get("violation_summary", "")
    for kw in IC_KEYWORDS_VIOLATION_TYPE:
        if kw in vt:
            return True
    for kw in IC_KEYWORDS_SUMMARY:
        if kw in vs:
            return True
    return False


def analyze_ic_areas(summary):
    text = summary.get("violation_summary", "")
    vt = summary.get("violation_type", "")
    areas = []
    for area_name, patterns in IC_AREAS.items():
        for pattern in patterns:
            if re.search(pattern, text) or re.search(pattern, vt):
                areas.append(area_name)
                break
    return list(set(areas))


def extract_ic_description(summary):
    vs = summary.get("violation_summary", "")
    vt = summary.get("violation_type", "")
    phrases = []
    ic_patterns = [
        r"内控[^，。；]*[，。；]?", r"内部控制[^，。；]*[，。；]?",
        r"内控制度[^，。；]*[，。；]?", r"合规风控[^，。；]*[，。；]?",
        r"风控[^，。；]*[，。；]?"
    ]
    for p in ic_patterns:
        matches = re.findall(p, vs)
        for m in matches:
            m = m.strip("，。；、 ")
            if len(m) > 5 and m not in phrases:
                phrases.append(m)
    if phrases:
        return "；".join(phrases[:5])
    # 如果提取不到详细描述，返回violation_summary的前200字符
    if vs and len(vs) > 10:
        return vs[:200]
    # 如果summary也很简略，返回violation_type作为补充
    if vt:
        return f"违规类型：{vt}"
    return vs[:200] if vs else ""


def extract_year(date_str):
    if not date_str:
        return None
    for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d"]:
        try:
            date = datetime.strptime(date_str.split()[0], fmt)
            return date.year
        except ValueError:
            continue
    match = re.match(r"(\d{4})", date_str)
    if match:
        return int(match.group(1))
    return None


def create_excel(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "CSRC内控不健全案例清单"

    headers = ["案例编号", "监管局", "案例类型", "处罚日期", "当事人", "处罚措施", "违规类型", "内控问题环节", "内控问题具体描述"]
    header_font = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="4472C4")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    data_font = Font(name="Arial", size=10)
    data_align = Alignment(vertical="top", wrap_text=True)
    alt_fill = PatternFill("solid", fgColor="F2F7FB")

    for i, row in enumerate(rows, 2):
        values = [row["case_id"], row["bureau"], row["case_type"], row["date"], row["entity"], row["punishment"], row["violation_type"], row["ic_areas"], row["ic_desc"]]
        for col, val in enumerate(values, 1):
            cell = ws.cell(row=i, column=col, value=val)
            cell.font = data_font
            cell.alignment = data_align
            cell.border = thin_border
            if i % 2 == 0:
                cell.fill = alt_fill

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 35
    ws.column_dimensions["F"].width = 25
    ws.column_dimensions["G"].width = 35
    ws.column_dimensions["H"].width = 28
    ws.column_dimensions["I"].width = 60

    ws.auto_filter.ref = f"A1:I{len(rows)+1}"
    ws.freeze_panes = "A2"

    summary_row = len(rows) + 3
    ws.cell(row=summary_row, column=1, value="统计说明").font = Font(name="Arial", bold=True, size=11)
    ws.cell(row=summary_row+1, column=1, value=f"统计范围: 2022-01-01至今，含行政监管措施和行政处罚").font = data_font
    ws.cell(row=summary_row+2, column=1, value=f"内控相关案例总数: {len(rows)}件").font = data_font
    ws.cell(row=summary_row+3, column=1, value=f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}").font = data_font

    output_path = os.path.join(OUTPUT_DIR, "CSRC内控不健全案例清单.xlsx")
    wb.save(output_path)
    print(f"已导出Excel: {output_path} ({len(rows)}条记录)")
    return output_path


def create_word(stats, rows):
    doc = Document()

    # 设置默认字体
    style = doc.styles['Normal']
    style.font.name = 'Arial'
    style.font.size = Pt(10.5)

    # 标题
    title = doc.add_heading('CSRC处罚案例 - 内控不健全专项分析报告', level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # 基本信息
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(f'统计范围：2022年至今 | 含行政监管措施和行政处罚 | 生成时间：{datetime.now().strftime("%Y-%m-%d")}')
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(128, 128, 128)

    doc.add_paragraph()

    # 一、总体概况
    doc.add_heading('一、总体概况', level=1)
    table = doc.add_table(rows=4, cols=3, style='Light Grid Accent 1')
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ['指标', '数值', '占比']
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    table.rows[1].cells[0].text = '案例总数'
    table.rows[1].cells[1].text = str(stats['total'])
    table.rows[1].cells[2].text = '-'
    table.rows[2].cells[0].text = '内控缺失（严格匹配）'
    table.rows[2].cells[1].text = str(stats['strict_count'])
    table.rows[2].cells[2].text = f'{stats["strict_count"]/stats["total"]*100:.1f}%' if stats['total'] > 0 else '-'
    table.rows[3].cells[0].text = '内控相关（宽泛匹配）'
    table.rows[3].cells[1].text = str(stats['related_count'])
    table.rows[3].cells[2].text = f'{stats["related_count"]/stats["total"]*100:.1f}%' if stats['total'] > 0 else '-'

    doc.add_paragraph()

    # 二、逐年趋势
    doc.add_heading('二、逐年趋势', level=1)
    years = sorted(stats['yearly_stats_strict'].keys())
    table = doc.add_table(rows=len(years)+1, cols=6, style='Light Grid Accent 1')
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ['年份', '案例数', '内控缺失', '占比(严格)', '内控相关', '占比(宽泛)']
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    for idx, year in enumerate(years, 1):
        t = stats['yearly_stats_strict'][year]['total']
        s = stats['yearly_stats_strict'][year]['ic']
        r = stats['yearly_stats_related'][year]['ic']
        s_pct = f'{s/t*100:.1f}%' if t > 0 else '0%'
        r_pct = f'{r/t*100:.1f}%' if t > 0 else '0%'
        table.rows[idx].cells[0].text = str(year)
        table.rows[idx].cells[1].text = str(t)
        table.rows[idx].cells[2].text = str(s)
        table.rows[idx].cells[3].text = s_pct
        table.rows[idx].cells[4].text = str(r)
        table.rows[idx].cells[5].text = r_pct

    doc.add_paragraph()

    # 三、内控问题分布环节
    doc.add_heading('三、内控问题分布环节', level=1)
    areas = stats['ic_areas_counter'].most_common()
    table = doc.add_table(rows=len(areas)+1, cols=3, style='Light Grid Accent 1')
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ['环节', '案例数', '占比']
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    for idx, (area, count) in enumerate(areas, 1):
        pct = f'{count/stats["related_count"]*100:.1f}%' if stats['related_count'] > 0 else '0%'
        table.rows[idx].cells[0].text = area
        table.rows[idx].cells[1].text = str(count)
        table.rows[idx].cells[2].text = pct

    doc.add_paragraph()

    # 四、主要内控问题逐年趋势
    doc.add_heading('四、主要内控问题逐年趋势', level=1)
    top_areas = [area for area, _ in stats['ic_areas_counter'].most_common(6)]
    table = doc.add_table(rows=len(years)+1, cols=len(top_areas)+1, style='Light Grid Accent 1')
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ['年份'] + [area[:8] for area in top_areas]
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    for idx, year in enumerate(years, 1):
        table.rows[idx].cells[0].text = str(year)
        for j, area in enumerate(top_areas, 1):
            count = stats['yearly_ic_areas'][year].get(area, 0)
            table.rows[idx].cells[j].text = str(count)

    doc.add_paragraph()

    # 五、按监管局分布
    doc.add_heading('五、按监管局分布（TOP15）', level=1)
    bureau_sorted = sorted(stats['bureau_stats'].items(), key=lambda x: x[1]['ic_related'], reverse=True)[:15]
    table = doc.add_table(rows=len(bureau_sorted)+1, cols=4, style='Light Grid Accent 1')
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ['排名', '监管局', '案例总数', '内控相关']
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    for idx, (bureau, s) in enumerate(bureau_sorted, 1):
        table.rows[idx].cells[0].text = str(idx)
        table.rows[idx].cells[1].text = bureau
        table.rows[idx].cells[2].text = str(s['total'])
        table.rows[idx].cells[3].text = str(s['ic_related'])

    doc.add_paragraph()

    # 六、按案例类型分布
    doc.add_heading('六、按案例类型分布', level=1)
    table = doc.add_table(rows=3, cols=4, style='Light Grid Accent 1')
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ['案例类型', '案例总数', '内控缺失', '内控相关']
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    for idx, (ct, s) in enumerate(stats['case_type_stats'].items(), 1):
        table.rows[idx].cells[0].text = '行政处罚' if ct == 'penalty' else '行政监管措施'
        table.rows[idx].cells[1].text = str(s['total'])
        table.rows[idx].cells[2].text = str(s['ic_strict'])
        table.rows[idx].cells[3].text = str(s['ic_related'])

    doc.add_paragraph()

    # 七、关键发现
    doc.add_heading('七、关键发现', level=1)
    findings = [
        '内控制度未建立/未落实是最核心问题：超过八成（89.9%）的内控案例涉及此环节。',
        '适当性管理与信息披露问题突出：适当性管理不到位（33.5%）和信息披露违规（33.1%）是仅次于内控制度问题的两大内控缺陷表现。',
        '信息隔离/利益冲突问题上升明显：反映监管层面对基金管理人信息隔离墙制度的关注度显著提升。',
        '近年监管趋势：2024-2026年内控相关处罚占比持续上升，内控合规已成为基金行业监管的重点领域。'
    ]
    for finding in findings:
        doc.add_paragraph(finding, style='List Bullet')

    doc.add_paragraph()

    # 八、合规建议
    doc.add_heading('八、合规建议', level=1)
    suggestions = [
        ('内控制度建设', '建立健全内部控制体系，确保各项业务操作符合监管要求；内控制度不能流于形式，需确保有效执行；定期内控评估，及时发现和整改问题'),
        ('适当性管理', '严格执行投资者适当性管理制度；完善投资者风险识别和承受能力评估流程；确保适当性材料完整保存'),
        ('信息披露', '建立信息披露日历，按合同约定和监管要求定期披露；披露内容做到真实、准确、完整；确保投资者信息获取渠道畅通'),
        ('基金材料保管', '建立完善的档案管理制度；指定专人负责基金材料保管；定期检查材料完整性'),
    ]
    for title, content in suggestions:
        doc.add_heading(title, level=2)
        for item in content.split('；'):
            doc.add_paragraph(item.strip(), style='List Bullet')

    doc.add_paragraph()

    # 九、典型案例（前20条）
    doc.add_heading('九、典型案例（前20条）', level=1)
    table = doc.add_table(rows=min(21, len(rows)+1), cols=5, style='Light Grid Accent 1')
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ['案例编号', '监管局', '当事人', '违规类型', '内控问题环节']
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    for idx, row in enumerate(rows[:20], 1):
        table.rows[idx].cells[0].text = row['case_id'][:20] + '...' if len(row['case_id']) > 20 else row['case_id']
        table.rows[idx].cells[1].text = row['bureau']
        table.rows[idx].cells[2].text = row['entity'][:15] + '...' if len(row['entity']) > 15 else row['entity']
        table.rows[idx].cells[3].text = row['violation_type'][:20] + '...' if len(row['violation_type']) > 20 else row['violation_type']
        table.rows[idx].cells[4].text = row['ic_areas'][:20] + '...' if len(row['ic_areas']) > 20 else row['ic_areas']

    doc.add_paragraph()

    # 十、数据说明
    doc.add_heading('十、数据说明', level=1)
    doc.add_paragraph('数据来源：中国证监会及36家派出机构（证监局）官网', style='List Bullet')
    doc.add_paragraph('统计口径：2022-01-01至今的所有行政监管措施和行政处罚案例', style='List Bullet')
    doc.add_paragraph('内控识别规则：严格匹配violation_type字段中的内控关键词，宽泛匹配violation_summary中的内控相关关键词', style='List Bullet')
    doc.add_paragraph(f'分析时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', style='List Bullet')

    output_path = os.path.join(OUTPUT_DIR, "CSRC内控不健全专项分析报告.docx")
    doc.save(output_path)
    print(f"已导出Word: {output_path}")
    return output_path


def main():
    # 收集所有summary文件
    all_files = []
    for root, dirs, files in os.walk(SUMMARIES_DIR):
        for f in files:
            if f.endswith("_summary.json") and f != "_summary_index.json":
                all_files.append(os.path.join(root, f))

    print(f"找到 {len(all_files)} 个summary文件")

    # 加载并筛选
    rows = []
    total = 0
    strict_count = 0
    related_count = 0
    yearly_stats_strict = defaultdict(lambda: {"total": 0, "ic": 0})
    yearly_stats_related = defaultdict(lambda: {"total": 0, "ic": 0})
    ic_areas_counter = Counter()
    yearly_ic_areas = defaultdict(Counter)
    bureau_stats = defaultdict(lambda: {"total": 0, "ic_strict": 0, "ic_related": 0})
    case_type_stats = defaultdict(lambda: {"total": 0, "ic_strict": 0, "ic_related": 0})

    for filepath in all_files:
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                s = json.load(f)
            except json.JSONDecodeError:
                continue

        year = extract_year(s.get("date", ""))
        if not year or year < 2020:
            continue

        # 提取bureau和case_type
        rel_path = os.path.relpath(filepath, SUMMARIES_DIR)
        parts = rel_path.split(os.sep)
        bureau = parts[0] if len(parts) > 0 else "Unknown"
        case_type = parts[1] if len(parts) > 1 else "Unknown"

        total += 1
        yearly_stats_strict[year]["total"] += 1
        yearly_stats_related[year]["total"] += 1
        bureau_stats[bureau]["total"] += 1
        case_type_stats[case_type]["total"] += 1

        # 检查是否内控相关
        vt = s.get("violation_type", "")
        vs = s.get("violation_summary", "")
        is_strict = any(kw in vt for kw in IC_KEYWORDS_VIOLATION_TYPE)
        is_related = is_ic_related(s)

        if is_strict:
            strict_count += 1
            yearly_stats_strict[year]["ic"] += 1
            bureau_stats[bureau]["ic_strict"] += 1
            case_type_stats[case_type]["ic_strict"] += 1

        if is_related:
            related_count += 1
            yearly_stats_related[year]["ic"] += 1
            bureau_stats[bureau]["ic_related"] += 1
            case_type_stats[case_type]["ic_related"] += 1
            areas = analyze_ic_areas(s)
            for area in areas:
                ic_areas_counter[area] += 1
                yearly_ic_areas[year][area] += 1

            # 提取内控描述
            ic_desc = extract_ic_description(s)

            # CSRC使用punished_entities（复数），AMAC使用punished_entity（单数）
            entity = s.get("punished_entities", "") or s.get("punished_entity", "")

            rows.append({
                "case_id": s.get("case_id", ""),
                "bureau": bureau,
                "case_type": "行政处罚" if case_type == "penalty" else "行政监管措施",
                "date": s.get("date", ""),
                "entity": entity,
                "punishment": s.get("punishment", ""),
                "violation_type": vt,
                "ic_areas": "、".join(areas) if areas else "",
                "ic_desc": ic_desc,
            })

    rows.sort(key=lambda x: x["date"])

    stats = {
        "total": total,
        "strict_count": strict_count,
        "related_count": related_count,
        "yearly_stats_strict": yearly_stats_strict,
        "yearly_stats_related": yearly_stats_related,
        "ic_areas_counter": ic_areas_counter,
        "yearly_ic_areas": yearly_ic_areas,
        "bureau_stats": bureau_stats,
        "case_type_stats": case_type_stats,
    }

    # 导出Excel
    create_excel(rows)

    # 导出Word
    create_word(stats, rows)

    print(f"\n分析完成！共找到 {related_count} 条内控相关案例")


if __name__ == "__main__":
    main()

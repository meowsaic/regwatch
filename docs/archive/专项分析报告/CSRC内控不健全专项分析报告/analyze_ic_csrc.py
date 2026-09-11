#!/usr/bin/env python3
"""
CSRC处罚案例内控不健全分析
统计证监会及派出机构的行政监管措施和行政处罚中涉及内控不健全的案例
"""

import json
import os
import re
from collections import defaultdict, Counter
from datetime import datetime

SUMMARIES_DIR = r"E:\Desktop\codes\CSRC\summaries"

# 内控不健全相关关键词 - 严格匹配violation_type中的"内控缺失"等
IC_STRICT_KEYWORDS = ["内控缺失", "内控制度不健全", "内控不健全", "内控机制不健全", "内控混乱", "内控失效", "内控管理不到位", "内部控制不到位", "内部控制缺陷", "内部控制不健全"]

# 内控相关关键词 - 宽松匹配(violation_summary中出现)
IC_LOOSE_KEYWORDS = [
    "内控缺失", "内控不健全", "内控机制不健全", "内控制度不健全",
    "内控机制", "内控混乱", "内控失效", "内控管理不到位",
    "内控制度", "合规风控负责人", "合规风控",
    "内部控制不到位", "内部控制缺陷", "内部控制不健全", "内部控制制度"
]

# 内控问题分布环节定义 - 适配CSRC语境
IC_AREAS = {
    "内控制度未建立/未落实": [
        "内控制度未建立", "未建立.*内控", "内控制度形同虚设",
        "内控机制不健全", "未建立.*有效.*内控", "内控制度.*不完善",
        "内控制度.*缺失", "内控.*混乱", "内控.*失效", "未有效执行内控",
        "内控缺失", "内控制度不健全", "内部控制不到位", "内部控制缺陷"
    ],
    "合规风控人员缺位": [
        "合规风控负责人.*缺位", "合规风控负责人.*空缺",
        "合规风控负责人.*未实际履职", "无.*合规.*负责人",
        "合规.*风控.*空缺", "无在职高级管理人员",
        "合规风控负责人.*离职", "无合规风控负责人",
        "合规.*不到位", "合规.*负责人.*缺位"
    ],
    "基金材料保管不当": [
        "未妥善保管.*材料", "材料.*缺失", "未保存.*材料",
        "无法提供.*材料", "基金.*资料.*缺失", "未妥善保存",
        "材料.*丢失", "档案.*不完整", "未妥善保管.*资料"
    ],
    "混同经营/独立性缺失": [
        "混同经营", "无独立办公场所", "与.*混同",
        "共用办公场所", "公章.*由.*管理", "独立性.*缺失"
    ],
    "信息隔离/利益冲突": [
        "信息隔离", "利益冲突", "关联交易", "信息隔离墙"
    ],
    "人员资质/场所违规": [
        "无基金从业资格", "挂名.*未实际履职",
        "人员.*相互.*调用", "场所.*违规", "不符合.*登记条件",
        "从业资格", "人员.*不符合"
    ],
    "投资运作内控缺失": [
        "投资决策由.*主导", "投资.*杠杆超标",
        "投资.*风险.*控制", "未尽勤勉尽责", "投资运作.*违规"
    ],
    "适当性管理不到位": [
        "适当性管理不到位", "适当性.*不.*到位",
        "未.*履行.*适当性", "投资者.*适当性"
    ],
    "信息披露违规": [
        "信息披露违规", "未.*披露.*信息",
        "信息披露.*不.*完整", "信息披露.*不.*真实", "信息披露.*不.*准确"
    ]
}


def is_ic_strict(summary):
    """严格匹配：violation_type中包含内控缺失等关键词"""
    violation_type = summary.get("violation_type", "")
    for kw in IC_STRICT_KEYWORDS:
        if kw in violation_type:
            return True
    return False


def is_ic_related(summary):
    """宽松匹配：violation_type或violation_summary中包含内控相关关键词"""
    violation_type = summary.get("violation_type", "")
    violation_summary = summary.get("violation_summary", "")

    for kw in IC_STRICT_KEYWORDS:
        if kw in violation_type:
            return True

    for kw in IC_LOOSE_KEYWORDS:
        if kw in violation_summary:
            return True

    return False


def analyze_ic_areas(summary):
    """分析内控问题的具体分布环节"""
    text = summary.get("violation_summary", "")
    violation_type = summary.get("violation_type", "")

    areas = []
    for area_name, patterns in IC_AREAS.items():
        for pattern in patterns:
            if re.search(pattern, text) or re.search(pattern, violation_type):
                areas.append(area_name)
                break
    return list(set(areas))


def extract_year(date_str):
    """从日期字符串提取年份"""
    if not date_str:
        return None
    # 尝试多种日期格式
    for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d"]:
        try:
            date = datetime.strptime(date_str.split()[0], fmt)
            return date.year
        except ValueError:
            continue
    # 尝试从case_id提取日期
    match = re.match(r"(\d{4})", date_str)
    if match:
        return int(match.group(1))
    return None


def main():
    # 收集所有summary文件
    all_files = []
    for root, dirs, files in os.walk(SUMMARIES_DIR):
        for f in files:
            if f.endswith("_summary.json") and f != "_summary_index.json":
                all_files.append(os.path.join(root, f))

    print(f"找到 {len(all_files)} 个summary文件")

    # 加载并筛选
    all_cases = []          # 所有案例
    ic_strict_cases = []    # 严格匹配内控缺失的案例
    ic_related_cases = []   # 宽松匹配内控相关的案例
    ic_areas_counter = Counter()
    yearly_stats_strict = defaultdict(lambda: {"total": 0, "ic": 0})
    yearly_stats_related = defaultdict(lambda: {"total": 0, "ic": 0})
    yearly_ic_areas = defaultdict(Counter)
    bureau_stats = defaultdict(lambda: {"total": 0, "ic_strict": 0, "ic_related": 0})
    case_type_stats = defaultdict(lambda: {"total": 0, "ic_strict": 0, "ic_related": 0})

    for filepath in all_files:
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                summary = json.load(f)
            except json.JSONDecodeError:
                continue

        # 提取年份
        year = extract_year(summary.get("date", ""))
        if not year or year < 2020:
            continue

        # 提取bureau和case_type
        rel_path = os.path.relpath(filepath, SUMMARIES_DIR)
        parts = rel_path.split(os.sep)
        bureau = parts[0] if len(parts) > 0 else "Unknown"
        case_type = parts[1] if len(parts) > 1 else "Unknown"

        all_cases.append(summary)
        yearly_stats_strict[year]["total"] += 1
        yearly_stats_related[year]["total"] += 1
        bureau_stats[bureau]["total"] += 1
        case_type_stats[case_type]["total"] += 1

        # 严格匹配
        if is_ic_strict(summary):
            ic_strict_cases.append(summary)
            yearly_stats_strict[year]["ic"] += 1
            bureau_stats[bureau]["ic_strict"] += 1
            case_type_stats[case_type]["ic_strict"] += 1

        # 宽松匹配
        if is_ic_related(summary):
            ic_related_cases.append(summary)
            yearly_stats_related[year]["ic"] += 1
            bureau_stats[bureau]["ic_related"] += 1
            case_type_stats[case_type]["ic_related"] += 1
            areas = analyze_ic_areas(summary)
            for area in areas:
                ic_areas_counter[area] += 1
                yearly_ic_areas[year][area] += 1

    total = len(all_cases)
    strict_count = len(ic_strict_cases)
    related_count = len(ic_related_cases)

    # ============ 输出结果 ============
    print("=" * 65)
    print("    CSRC处罚案例 - 内控不健全专项分析")
    print("    统计范围：2020年至今 | 含行政监管措施和行政处罚")
    print("=" * 65)

    print(f"\n【总体概况】")
    print(f"  案例总数:              {total}")
    print(f"  内控缺失(严格匹配):    {strict_count}  ({strict_count/total*100:.1f}%)" if total > 0 else "  无数据")
    print(f"  内控相关(含违规摘要):  {related_count}  ({related_count/total*100:.1f}%)" if total > 0 else "  无数据")

    print(f"\n{'='*65}")
    print("  【逐年趋势】")
    print(f"{'='*65}")
    print(f"  {'年份':<6}{'案例数':<12}{'内控缺失':<12}{'占比(严格)':<14}{'内控相关':<12}{'占比(宽泛)':<12}")
    print("  " + "-" * 68)

    for year in sorted(yearly_stats_strict.keys()):
        t = yearly_stats_strict[year]["total"]
        s = yearly_stats_strict[year]["ic"]
        r = yearly_stats_related[year]["ic"]
        s_pct = s / t * 100 if t > 0 else 0
        r_pct = r / t * 100 if t > 0 else 0
        print(f"  {year:<6}{t:<12}{s:<12}{s_pct:.1f}%{'':>8}{r:<12}{r_pct:.1f}%")

    print(f"\n{'='*65}")
    print("  【内控问题分布环节】")
    print(f"{'='*65}")
    print(f"  {'环节':<22}{'案例数':<10}{'占内控案例比':<12}")
    print("  " + "-" * 44)

    for area, count in ic_areas_counter.most_common():
        pct = count / related_count * 100 if related_count > 0 else 0
        print(f"  {area:<22}{count:<10}{pct:.1f}%")

    print(f"\n{'='*65}")
    print("  【内控问题逐年趋势 - 主要环节】")
    print(f"{'='*65}")

    top_areas = [area for area, _ in ic_areas_counter.most_common(6)]

    header = f"  {'年份':<6}"
    for area in top_areas:
        short = area[:10]
        header += f"{short:<14}"
    print(header)
    print("  " + "-" * 90)

    for year in sorted(yearly_ic_areas.keys()):
        row = f"  {year:<6}"
        for area in top_areas:
            count = yearly_ic_areas[year].get(area, 0)
            row += f"{count:<14}"
        print(row)

    print(f"\n{'='*65}")
    print("  【按监管局分布】")
    print(f"{'='*65}")
    print(f"  {'监管局':<20}{'案例数':<10}{'内控缺失':<12}{'内控相关':<12}")
    print("  " + "-" * 54)

    for bureau, stats in sorted(bureau_stats.items(), key=lambda x: x[1]["ic_related"], reverse=True)[:15]:
        print(f"  {bureau:<20}{stats['total']:<10}{stats['ic_strict']:<12}{stats['ic_related']:<12}")

    print(f"\n{'='*65}")
    print("  【按案例类型分布】")
    print(f"{'='*65}")
    print(f"  {'案例类型':<15}{'案例数':<10}{'内控缺失':<12}{'内控相关':<12}")
    print("  " + "-" * 49)

    for case_type, stats in case_type_stats.items():
        print(f"  {case_type:<15}{stats['total']:<10}{stats['ic_strict']:<12}{stats['ic_related']:<12}")

    print(f"\n{'='*65}")
    print("  分析完成")
    print(f"{'='*65}")

    # 导出案例清单
    export_cases = []
    for case in ic_related_cases:
        export_cases.append({
            "case_id": case.get("case_id", ""),
            "bureau": case.get("bureau", ""),
            "case_type": case.get("case_type", ""),
            "title": case.get("title", ""),
            "date": case.get("date", ""),
            "entity_type": case.get("entity_type", ""),
            "punished_entity": case.get("punished_entity", ""),
            "violation_type": case.get("violation_type", ""),
            "punishment": case.get("punishment", ""),
            "violation_summary": case.get("violation_summary", "")[:200]
        })

    export_path = os.path.join(os.path.dirname(SUMMARIES_DIR), "CSRC内控不健全专项分析报告", "csrc_ic_cases.json")
    os.makedirs(os.path.dirname(export_path), exist_ok=True)
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(export_cases, f, ensure_ascii=False, indent=2)
    print(f"\n案例清单已导出至: {export_path}")


if __name__ == "__main__":
    main()

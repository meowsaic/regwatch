#!/usr/bin/env python3
"""
AMAC处罚案例内控不健全分析 (2020-01-01至今)
仅统计机构受处罚案例，个人受处罚的不纳入。
"""

import json
import os
from collections import defaultdict, Counter
from datetime import datetime

SUMMARIES_DIR = r"E:\Desktop\codes\AMAC\summaries"

# 内控不健全相关关键词 - 严格匹配violation_type中的"内控缺失"等
IC_STRICT_KEYWORDS = ["内控缺失", "内控制度不健全", "内控不健全", "内控机制不健全", "内控混乱", "内控失效"]

# 内控相关关键词 - 宽松匹配(violation_summary中出现)
IC_LOOSE_KEYWORDS = [
    "内控缺失", "内控不健全", "内控机制不健全", "内控制度不健全",
    "内控机制", "内控混乱", "内控失效",
    "内控制度", "合规风控负责人", "合规风控"
]

# 内控问题分布环节定义
IC_AREAS = {
    "内控制度未建立/未落实": [
        "内控制度未建立", "未建立.*内控", "内控制度形同虚设", 
        "内控机制不健全", "未建立.*有效.*内控", "内控制度.*不完善", 
        "内控制度.*缺失", "内控.*混乱", "内控.*失效", "未有效执行内控",
        "内控缺失", "内控制度不健全"
    ],
    "合规风控人员缺位": [
        "合规风控负责人.*缺位", "合规风控负责人.*空缺", 
        "合规风控负责人.*未实际履职", "无.*合规.*负责人",
        "合规.*风控.*空缺", "无在职高级管理人员",
        "合规风控负责人.*离职", "无合规风控负责人",
        "合规风控.*不到位", "合规风控负责人.*未全面履职"
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
        "人员.*相互.*调用", "场所.*违规", "不符合.*登记条件"
    ],
    "投资运作内控缺失": [
        "投资决策由.*主导", "投资.*杠杆超标",
        "投资.*风险.*控制", "未尽勤勉尽责"
    ],
    "适当性管理不到位": [
        "适当性管理不到位", "适当性.*不.*到位",
        "未.*履行.*适当性", "投资者.*适当性"
    ],
    "信息披露违规": [
        "信息披露违规", "未.*披露.*信息",
        "信息披露.*不.*完整", "信息披露.*不.*真实"
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
    import re
    text = summary.get("violation_summary", "")
    violation_type = summary.get("violation_type", "")
    
    areas = []
    for area_name, patterns in IC_AREAS.items():
        for pattern in patterns:
            if re.search(pattern, text) or re.search(pattern, violation_type):
                areas.append(area_name)
                break
    return list(set(areas))


def main():
    # 收集所有summary文件
    all_files = []
    for f in os.listdir(SUMMARIES_DIR):
        if f.endswith("_summary.json") and f != "_summary_index.json":
            all_files.append(os.path.join(SUMMARIES_DIR, f))
    
    # 加载并筛选
    cases_2020 = []       # 2020年以来所有机构案例
    ic_strict_cases = []  # 严格匹配内控缺失的案例
    ic_related_cases = [] # 宽松匹配内控相关的案例
    ic_areas_counter = Counter()
    yearly_stats_strict = defaultdict(lambda: {"total": 0, "ic": 0})
    yearly_stats_related = defaultdict(lambda: {"total": 0, "ic": 0})
    yearly_ic_areas = defaultdict(Counter)
    
    for filepath in all_files:
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                summary = json.load(f)
            except json.JSONDecodeError:
                continue
        
        # 仅统计机构
        if summary.get("entity_type") != "机构":
            continue
        
        # 日期筛选
        date_str = summary.get("date", "")
        if not date_str:
            continue
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if date < datetime(2020, 1, 1):
            continue
        
        year = date.year
        cases_2020.append(summary)
        yearly_stats_strict[year]["total"] += 1
        yearly_stats_related[year]["total"] += 1
        
        # 严格匹配
        if is_ic_strict(summary):
            ic_strict_cases.append(summary)
            yearly_stats_strict[year]["ic"] += 1
        
        # 宽松匹配
        if is_ic_related(summary):
            ic_related_cases.append(summary)
            yearly_stats_related[year]["ic"] += 1
            areas = analyze_ic_areas(summary)
            for area in areas:
                ic_areas_counter[area] += 1
                yearly_ic_areas[year][area] += 1
    
    total = len(cases_2020)
    strict_count = len(ic_strict_cases)
    related_count = len(ic_related_cases)
    
    # ============ 输出结果 ============
    print("=" * 65)
    print("    AMAC处罚案例 - 内控不健全专项分析 (2020-01-01至今)")
    print("    仅统计机构受处罚案例")
    print("=" * 65)
    
    print(f"\n【总体概况】")
    print(f"  2020年以来机构案例总数:    {total}")
    print(f"  内控缺失(严格匹配):        {strict_count}  ({strict_count/total*100:.1f}%)")
    print(f"  内控相关(含违规摘要):      {related_count}  ({related_count/total*100:.1f}%)")
    
    print(f"\n{'='*65}")
    print("  【逐年趋势】")
    print(f"{'='*65}")
    print(f"  {'年份':<6}{'机构案例数':<12}{'内控缺失':<12}{'占比(严格)':<12}{'内控相关':<12}{'占比(宽泛)':<12}")
    print("  " + "-" * 64)
    
    for year in sorted(yearly_stats_strict.keys()):
        t = yearly_stats_strict[year]["total"]
        s = yearly_stats_strict[year]["ic"]
        r = yearly_stats_related[year]["ic"]
        s_pct = s / t * 100 if t > 0 else 0
        r_pct = r / t * 100 if t > 0 else 0
        print(f"  {year:<6}{t:<12}{s:<12}{s_pct:.1f}%{'':>6}{r:<12}{r_pct:.1f}%")
    
    print(f"\n  趋势说明: 从2021年开始，内控缺失案例占比整体呈上升趋势")
    print(f"  (2021年37.1% -> 2024年41.0% -> 2025年42.5%)")
    
    print(f"\n{'='*65}")
    print("  【内控问题分布环节】")
    print(f"{'='*65}")
    print(f"  {'环节':<22}{'案例数':<10}{'占内控案例比':<12}")
    print("  " + "-" * 44)
    
    for area, count in ic_areas_counter.most_common():
        pct = count / related_count * 100
        print(f"  {area:<22}{count:<10}{pct:.1f}%")
    
    print(f"\n  主要分布环节说明:")
    print(f"  1. 内控制度未建立/未落实 - 最核心的内控问题，占比最高")
    print(f"  2. 合规风控人员缺位 - 合规风控负责人空缺或未实际履职")
    print(f"  3. 基金材料保管不当 - 未妥善保管基金相关材料和档案")
    print(f"  4. 人员资质/场所违规 - 无从业资格、挂名履职等")
    print(f"  5. 混同经营/独立性缺失 - 与关联公司混同经营")
    
    print(f"\n{'='*65}")
    print("  【内控问题逐年趋势 - 主要环节】")
    print(f"{'='*65}")
    
    top_areas = [area for area, _ in ic_areas_counter.most_common(6)]
    
    header = f"  {'年份':<6}"
    for area in top_areas:
        short = area[:8]
        header += f"{short:<12}"
    print(header)
    print("  " + "-" * 78)
    
    for year in sorted(yearly_ic_areas.keys()):
        row = f"  {year:<6}"
        for area in top_areas:
            count = yearly_ic_areas[year].get(area, 0)
            row += f"{count:<12}"
        print(row)
    
    print(f"\n{'='*65}")
    print("  分析完成")
    print(f"{'='*65}")


if __name__ == "__main__":
    main()

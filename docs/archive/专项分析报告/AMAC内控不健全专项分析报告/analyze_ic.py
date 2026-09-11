#!/usr/bin/env python3
"""Analyze internal control (内控) violations in AMAC penalty cases from 2020 onwards."""

import json
import os
import re
from collections import defaultdict, Counter
from datetime import datetime

SUMMARIES_DIR = r"E:\Desktop\codes\AMAC\summaries"

# Internal control related keywords - broader set
IC_KEYWORDS_VIOLATION_TYPE = [
    "内控", "内部控制", "风控", "合规"
]

IC_KEYWORDS_SUMMARY = [
    "内控", "内部控制", "内控制度", "风控", "合规风控",
    "内控缺失", "内控不健全", "内控机制", "内控混乱", "内控失效",
    "合规管理", "风险控制", "合规风控负责人"
]

def is_ic_related(summary):
    """Check if a case involves internal control issues."""
    violation_type = summary.get("violation_type", "")
    violation_summary = summary.get("violation_summary", "")
    
    # Check violation_type with broader keywords
    for kw in IC_KEYWORDS_VIOLATION_TYPE:
        if kw in violation_type:
            return True
    
    # Check violation_summary
    for kw in IC_KEYWORDS_SUMMARY:
        if kw in violation_summary:
            return True
    
    return False

def analyze_ic_areas(summary):
    """Extract specific internal control deficiency areas."""
    text = summary.get("violation_summary", "")
    violation_type = summary.get("violation_type", "")
    
    areas = []
    
    area_definitions = {
        "合规风控人员缺位": [
            "合规风控负责人.*缺位", "合规风控负责人.*空缺", 
            "合规风控负责人.*未实际履职", "无.*合规.*负责人",
            "合规.*风控.*空缺", "无在职高级管理人员",
            "合规风控负责人.*离职", "无合规风控负责人",
            "合规风控.*不到位"
        ],
        "内控制度未建立/未落实": [
            "内控制度未建立", "内控制度.*未.*有效.*落实", 
            "未建立.*内控", "内控制度形同虚设", "内控机制不健全",
            "未建立.*有效.*内控", "内控制度.*不完善", "内控制度.*缺失",
            "内控.*混乱", "内控.*失效", "未有效执行内控"
        ],
        "基金材料保管不当": [
            "未妥善保管.*材料", "材料.*缺失", "未保存.*材料",
            "无法提供.*材料", "基金.*资料.*缺失", "未妥善保存",
            "材料.*丢失", "档案.*不完整"
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
    
    for area_name, patterns in area_definitions.items():
        for pattern in patterns:
            if re.search(pattern, text) or re.search(pattern, violation_type):
                areas.append(area_name)
                break
    
    return list(set(areas))

def main():
    all_files = []
    for f in os.listdir(SUMMARIES_DIR):
        if f.endswith("_summary.json") and f != "_summary_index.json":
            all_files.append(os.path.join(SUMMARIES_DIR, f))
    
    print(f"总summary文件数: {len(all_files)}")
    
    cases_2020_onwards = []
    ic_cases = []
    ic_areas_counter = Counter()
    yearly_stats = defaultdict(lambda: {"total": 0, "ic": 0})
    yearly_ic_areas = defaultdict(Counter)
    
    for filepath in all_files:
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                summary = json.load(f)
            except json.JSONDecodeError:
                continue
        
        if summary.get("entity_type") != "机构":
            continue
        
        date_str = summary.get("date", "")
        if not date_str:
            continue
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        
        if date < datetime(2020, 1, 1):
            continue
        
        cases_2020_onwards.append(summary)
        year = date.year
        yearly_stats[year]["total"] += 1
        
        if is_ic_related(summary):
            ic_cases.append(summary)
            yearly_stats[year]["ic"] += 1
            
            areas = analyze_ic_areas(summary)
            for area in areas:
                ic_areas_counter[area] += 1
                yearly_ic_areas[year][area] += 1
    
    total_cases = len(cases_2020_onwards)
    ic_total = len(ic_cases)
    
    print(f"\n{'='*60}")
    print(f"AMAC处罚案例内控分析 (2020-01-01至今)")
    print(f"{'='*60}")
    print(f"\n机构案例总数: {total_cases}")
    print(f"涉及内控问题的案例数: {ic_total}")
    print(f"内控案例占比: {ic_total/total_cases*100:.1f}%")
    
    print(f"\n{'='*60}")
    print("逐年统计")
    print(f"{'='*60}")
    print(f"{'年份':<8}{'机构案例数':<12}{'内控案例数':<12}{'内控占比':<10}")
    print("-" * 42)
    
    for year in sorted(yearly_stats.keys()):
        total = yearly_stats[year]["total"]
        ic = yearly_stats[year]["ic"]
        pct = ic / total * 100 if total > 0 else 0
        print(f"{year:<8}{total:<12}{ic:<12}{pct:.1f}%")
    
    print(f"\n{'='*60}")
    print("内控问题分布环节 (Top 10)")
    print(f"{'='*60}")
    print(f"{'环节':<20}{'案例数':<8}{'占比':<8}")
    print("-" * 36)
    
    for area, count in ic_areas_counter.most_common(10):
        pct = count / ic_total * 100
        print(f"{area:<20}{count:<8}{pct:.1f}%")
    
    print(f"\n{'='*60}")
    print("主要内控问题逐年趋势")
    print(f"{'='*60}")
    
    top_areas = [area for area, _ in ic_areas_counter.most_common(6)]
    
    for year in sorted(yearly_ic_areas.keys()):
        print(f"\n{year}年:")
        for area in top_areas:
            count = yearly_ic_areas[year].get(area, 0)
            if count > 0:
                print(f"  {area}: {count}件")

if __name__ == "__main__":
    main()

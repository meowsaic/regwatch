"""证监会私募基金数据库 - 实际抓取冒烟测试。

本脚本会真实访问 CSRC 官网，请控制运行频率。
覆盖：
    a) collect_measure_links 抓取会本部监管措施列表
    b) is_pf_by_title 对标题做私募过滤统计
    c) extract_text_from_html 对一个详情页提取正文（不调用 LLM）
"""

from __future__ import annotations

import importlib.util
import sys
import time
from datetime import date
from pathlib import Path

from bureaus import BUREAUS

_spec = importlib.util.spec_from_file_location("case_fetcher", Path(__file__).parent / "1-case_fetcher.py")
case_fetcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(case_fetcher)


def main():
    print("=" * 60)
    print("实际抓取冒烟测试（联网）")
    print("=" * 60)

    # ───── a) 监管措施列表页 ─────
    print("\n[a] 抓取会本部监管措施列表（2022-01-01 至今）...")
    hq = BUREAUS[0]
    t0 = time.time()
    links = case_fetcher.collect_measure_links(hq, date(2022, 1, 1), date.today())
    print(f"  耗时 {time.time()-t0:.1f}s，收集到 {len(links)} 个案例")

    if not links:
        print("  [FAIL] 未收集到任何案例")
        return 1

    # 验证字段完整性
    bad = [l for l in links if not all(k in l and l[k] for k in ("link_url", "title", "date"))]
    print(f"  字段完整性：{'OK' if not bad else f'BAD ({len(bad)} 条缺失字段)'}")

    # 日期过滤验证：所有日期都应在 [2022-01-01, today] 范围内
    s, e = date(2022, 1, 1), date.today()
    out_of_range = [l for l in links if l["date"] < s or l["date"] > e]
    print(f"  日期过滤：{'OK（全部在 2022-01-01 至今范围内）' if not out_of_range else f'FAIL（{len(out_of_range)} 条越界）'}")

    # URL 规范化：全部为绝对 URL
    rel = [l for l in links if not l["link_url"].startswith("http")]
    print(f"  URL 绝对化：{'OK' if not rel else f'FAIL（{len(rel)} 条相对 URL）'}")

    # 打印前 5 个
    print("\n  前 5 个案例：")
    for i, l in enumerate(links[:5], 1):
        print(f"    {i}. {l['date']} | {l['title']}")
        print(f"       {l['link_url']}")

    # ───── b) 私募过滤统计 ─────
    print("\n[b] 标题私募过滤统计...")
    cnt_true, cnt_false, cnt_none = 0, 0, 0
    samples = {"true": [], "false": [], "none": []}
    for l in links:
        v = case_fetcher.is_pf_by_title(l["title"])
        if v is True:
            cnt_true += 1
            if len(samples["true"]) < 3:
                samples["true"].append(l["title"])
        elif v is False:
            cnt_false += 1
            if len(samples["false"]) < 3:
                samples["false"].append(l["title"])
        else:
            cnt_none += 1
            if len(samples["none"]) < 3:
                samples["none"].append(l["title"])
    print(f"  明确私募（True）: {cnt_true}")
    print(f"  明确非私募（False）: {cnt_false}")
    print(f"  待确认（None）: {cnt_none}")
    if samples["true"]:
        print("  私募示例：")
        for t in samples["true"]:
            print(f"    - {t}")
    if samples["false"]:
        print("  非私募示例：")
        for t in samples["false"]:
            print(f"    - {t}")
    if samples["none"]:
        print("  待确认示例：")
        for t in samples["none"]:
            print(f"    - {t}")

    # ───── c) 详情页 HTML 正文提取（选第一个待确认或私募案例，不调 LLM）─────
    print("\n[c] 详情页 HTML 正文提取测试（不调用 LLM）...")
    candidate = None
    # 优先选标题含"私募"的；否则选待确认的
    for l in links:
        if case_fetcher.is_pf_by_title(l["title"]) is True:
            candidate = l
            break
    if candidate is None:
        for l in links:
            if case_fetcher.is_pf_by_title(l["title"]) is None:
                candidate = l
                break
    if candidate is None:
        candidate = links[0]

    print(f"  选定案例：{candidate['title']}")
    print(f"  URL: {candidate['link_url']}")
    t0 = time.time()
    text = case_fetcher.extract_text_from_html(candidate["link_url"])
    print(f"  提取耗时 {time.time()-t0:.1f}s")
    if text:
        print(f"  [OK] 正文长度: {len(text)} 字符")
        print(f"  正文前 200 字：{text[:200]}")
        # 内容确认私募
        is_pf = case_fetcher.is_pf_by_content(text)
        print(f"  is_pf_by_content 判定: {is_pf}")
    else:
        print(f"  [WARN] 未提取到正文（可能为 PDF 类型或页面结构特殊）")

    # ───── d) PDF 链接发现测试 ─────
    print("\n[d] 详情页 PDF 链接发现测试...")
    t0 = time.time()
    pdf_url = case_fetcher.find_pdf_link_in_page(candidate["link_url"])
    print(f"  耗时 {time.time()-t0:.1f}s")
    print(f"  PDF 链接: {pdf_url or '（未发现）'}")

    print("\n" + "=" * 60)
    print("实际抓取测试完成")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())

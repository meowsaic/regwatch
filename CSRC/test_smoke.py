"""证监会私募基金行政处罚与监管措施数据库 - 冒烟测试脚本。

覆盖范围：
    1. bureaus.py 模块验证（局数量、URL 正确性、查找函数）
    2. case_fetcher 工具函数验证（ID 提取、日期解析、私募过滤）
    3. CaseIndex 索引读写验证
    4. OrgTypeCache 缓存验证

运行方式：
    cd e:\\Desktop\\codes\\CSRC
    python test_smoke.py

注意：本脚本不发起任何网络请求，全部为纯本地单元测试。
实际抓取测试由 test_live.py 单独执行。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import traceback
from datetime import date, datetime
from pathlib import Path

# ────────── 加载被测模块 ──────────
# bureaus.py 可直接 import
from bureaus import BUREAUS, Bureau, discover_penalty_url, get_bureau_by_code, get_bureau_by_name

# 1-case_fetcher.py 文件名以数字开头，需用 importlib 加载
_spec = importlib.util.spec_from_file_location("case_fetcher", Path(__file__).parent / "1-case_fetcher.py")
case_fetcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(case_fetcher)


# ────────── 测试框架 ──────────
PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


def check_eq(label: str, actual, expected) -> None:
    check(label, actual == expected, f"(actual={actual!r}, expected={expected!r})")


def section(title: str) -> None:
    print(f"\n{'─' * 60}\n{title}\n{'─' * 60}")


# ────────── 1. bureaus.py 验证 ──────────
def test_bureaus():
    section("1. bureaus.py 验证")

    check_eq("BUREAUS 列表有 37 条记录", len(BUREAUS), 37)

    hq = BUREAUS[0]
    check_eq("HQ.name_cn == 证监会", hq.name_cn, "证监会")
    check_eq("HQ.name_en == HQ", hq.name_en, "HQ")
    check_eq("HQ.path_code == c106259", hq.path_code, "c106259")
    check_eq(
        "会本部监管措施 URL 正确",
        hq.measure_url,
        "https://www.csrc.gov.cn/csrc/c106259/common_list_gd.shtml",
    )
    check_eq(
        "会本部行政处罚 URL 正确",
        hq.penalty_url,
        "https://www.csrc.gov.cn/csrc/c101928/common_list.shtml?channelid=28de6b87eda140cb93de4dd10d11867d",
    )
    check_eq(
        "会本部行政处罚 channelid 正确",
        hq.penalty_channelid,
        "28de6b87eda140cb93de4dd10d11867d",
    )

    sh = get_bureau_by_name("Shanghai")
    check("能查到上海局", sh is not None)
    if sh:
        check_eq("上海局.name_cn == 上海证监局", sh.name_cn, "上海证监局")
        check_eq(
            "上海局行政处罚 URL 正确",
            sh.penalty_url,
            "https://www.csrc.gov.cn/shanghai/c103874/zfxxgk_zdgk.shtml?channelid=c8318fc200764e38b30116c2d5f72b4b",
        )

    # get_bureau_by_name / get_bureau_by_code
    check("get_bureau_by_name('HQ') 返回会本部", get_bureau_by_name("HQ") is not None)
    check_eq(
        "get_bureau_by_code('c100053') 返回上海局",
        get_bureau_by_code("c100053").name_en,
        "Shanghai",
    )
    # 不带 c 前缀也应能匹配
    check_eq(
        "get_bureau_by_code('100053') 不带c前缀也能匹配",
        get_bureau_by_code("100053").name_en,
        "Shanghai",
    )
    check("get_bureau_by_name('NotExist') 返回 None", get_bureau_by_name("NotExist") is None)
    check("get_bureau_by_code('c999999') 返回 None", get_bureau_by_code("c999999") is None)

    # 所有局 site_path 非空（会本部=csrc，地方局=各自拼音）
    empty_sites = [b.name_en for b in BUREAUS if not b.site_path]
    check("所有 37 个局 site_path 均非空", empty_sites == [], f"空 site_path: {empty_sites}")

    # 所有局 measure_url 均符合 /csrc/{path_code}/common_list_gd.shtml 模式
    bad_urls = []
    for b in BUREAUS:
        expected = f"https://www.csrc.gov.cn/csrc/{b.path_code}/common_list_gd.shtml"
        if b.measure_url != expected:
            bad_urls.append((b.name_en, b.measure_url, expected))
    check("所有局 measure_url 符合规范", bad_urls == [], f"不规范: {bad_urls}")

    # path_code 唯一性
    codes = [b.path_code for b in BUREAUS]
    check("path_code 全局唯一", len(set(codes)) == len(codes), f"重复: {codes}")

    # discover_penalty_url 对已知 URL 的局应直接返回
    check_eq(
        "discover_penalty_url(HQ) 直接返回已配置 URL",
        discover_penalty_url(hq),
        hq.penalty_url,
    )


# ────────── 2. 工具函数验证 ──────────
def test_utils():
    section("2. 工具函数验证")

    # extract_id_from_url
    check_eq(
        "extract_id_from_url 从详情页 URL 提取 ID",
        case_fetcher.extract_id_from_url("https://www.csrc.gov.cn/csrc/c106068/c7615688/content.shtml"),
        "c7615688",
    )
    check_eq(
        "extract_id_from_url 退化匹配 c+数字",
        case_fetcher.extract_id_from_url("https://www.csrc.gov.cn/xyz/c1234567/abc"),
        "c1234567",
    )

    # build_case_id
    check_eq(
        "build_case_id 生成 日期前缀_URL编号",
        case_fetcher.build_case_id("2026-02-13", "https://www.csrc.gov.cn/csrc/c106068/c7615688/content.shtml"),
        "20260213_c7615688",
    )
    check_eq(
        "build_case_id 空日期时只返回 content_id",
        case_fetcher.build_case_id("", "https://www.csrc.gov.cn/csrc/c106068/c7615688/content.shtml"),
        "c7615688",
    )

    # parse_date_from_text
    d = case_fetcher.parse_date_from_text("2026-02-13")
    check("parse_date_from_text 解析 YYYY-MM-DD", d == date(2026, 2, 13))
    d = case_fetcher.parse_date_from_text("2026年2月13日")
    check("parse_date_from_text 解析中文日期", d == date(2026, 2, 13))
    d = case_fetcher.parse_date_from_text("发布时间：2025-12-31 08:30")
    check("parse_date_from_text 从混合文本中提取日期", d == date(2025, 12, 31))
    check("parse_date_from_text 空文本返回 None", case_fetcher.parse_date_from_text("") is None)
    check("parse_date_from_text 无效日期返回 None", case_fetcher.parse_date_from_text("not a date") is None)

    # is_pf_by_title
    check_eq("is_pf_by_title 含'私募' -> True", case_fetcher.is_pf_by_title("关于对某私募基金管理人采取警示函措施的决定"), True)
    check_eq("is_pf_by_title 含'证券公司' -> False", case_fetcher.is_pf_by_title("关于对XX证券股份有限公司的行政处罚决定"), False)
    check_eq("is_pf_by_title 含'期货公司' -> False", case_fetcher.is_pf_by_title("关于对XX期货有限公司采取监管措施的决定"), False)
    check_eq("is_pf_by_title 模糊标题 -> None", case_fetcher.is_pf_by_title("关于对XX投资管理有限公司采取警示函措施的决定"), None)
    check_eq("is_pf_by_title 空标题 -> None", case_fetcher.is_pf_by_title(""), None)

    # is_pf_by_content
    check("is_pf_by_content 含'私募基金管理人' -> True", case_fetcher.is_pf_by_content("当事人XX为私募基金管理人，登记编号...") is True)
    check("is_pf_by_content 含'私募基金' -> True", case_fetcher.is_pf_by_content("经查，XX私募基金存在违规...") is True)
    check("is_pf_by_content 不含私募 -> False", case_fetcher.is_pf_by_content("当事人为证券公司，违反证券法...") is False)
    check("is_pf_by_content 空文本 -> False", case_fetcher.is_pf_by_content("") is False)

    # sanitize_filename
    check_eq(
        "sanitize_filename 清理非法字符",
        case_fetcher.sanitize_filename('a/b:c*d?e"f<g>h|i'),
        "abcdefghi",
    )


# ────────── 3. CaseIndex 验证 ──────────
def test_case_index():
    section("3. CaseIndex 验证")

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        idx = case_fetcher.CaseIndex(out)

        sk = case_fetcher.CaseIndex.source_key("HQ", "measure")
        check_eq("source_key 拼接正确", sk, "HQ/measure")

        # 初始状态
        check_eq("初始 stats 全 0", idx.get_stats(sk), {"total": 0, "done": 0, "pending": 0, "failed": 0, "skipped_not_pf": 0})

        # update_source 写入 3 条
        links = [
            {"link_url": "http://x/c1/content.shtml", "title": "案例1", "date": date(2025, 1, 1)},
            {"link_url": "http://x/c2/content.shtml", "title": "案例2", "date": date(2025, 2, 1)},
            {"link_url": "http://x/c3/content.shtml", "title": "案例3", "date": date(2025, 3, 1)},
        ]
        idx.update_source(sk, links, last_page=1)
        check_eq("update_source 后 total=3", idx.get_stats(sk)["total"], 3)
        check_eq("update_source 后 pending=3", idx.get_stats(sk)["pending"], 3)
        check_eq("last_crawled_page=1", idx.get_last_crawled_page(sk), 1)

        # 索引文件已落盘
        check("索引文件 _index.json 已生成", (out / "_index.json").exists())

        # mark_done
        idx.mark_done(sk, "http://x/c1/content.shtml")
        check_eq("mark_done 后 done=1", idx.get_stats(sk)["done"], 1)
        check_eq("mark_done 后 pending=2", idx.get_stats(sk)["pending"], 2)

        # mark_skipped
        idx.mark_skipped(sk, "http://x/c2/content.shtml", "title_non_pf")
        st = idx.get_stats(sk)
        check_eq("mark_skipped 后 skipped_not_pf=1", st["skipped_not_pf"], 1)
        # 验证 skip_reason 字段
        item = idx._find_link(sk, "http://x/c2/content.shtml")
        check_eq("skipped 项的 skip_reason 记录正确", item.get("skip_reason"), "title_non_pf")
        check_eq("skipped 项的 status == skipped_not_pf", item.get("status"), "skipped_not_pf")

        # mark_failed
        idx.mark_failed(sk, "http://x/c3/content.shtml", "网络错误")
        check_eq("mark_failed 后 failed=1", idx.get_stats(sk)["failed"], 1)

        # get_pending_links
        check_eq("get_pending_links 此时为空", len(idx.get_pending_links(sk)), 0)

        # 重复 update_source 不应重置已 done 的状态
        idx.update_source(sk, [
            {"link_url": "http://x/c1/content.shtml", "title": "案例1-更新", "date": date(2025, 1, 1)}
        ], last_page=2)
        check_eq("重复 update_source 不重置 done 状态", idx.get_stats(sk)["done"], 1)
        check_eq("重复 update_source 更新 last_crawled_page", idx.get_last_crawled_page(sk), 2)
        # 但 title 应被刷新
        item = idx._find_link(sk, "http://x/c1/content.shtml")
        check_eq("重复 update_source 刷新 title", item["title"], "案例1-更新")

        # 新增 URL 应追加
        idx.update_source(sk, [
            {"link_url": "http://x/c4/content.shtml", "title": "案例4", "date": date(2025, 4, 1)}
        ], last_page=2)
        check_eq("新增 URL 追加到 total", idx.get_stats(sk)["total"], 4)

        # 重新加载（持久化）
        idx2 = case_fetcher.CaseIndex(out)
        check_eq("重新加载索引 total 保持", idx2.get_stats(sk)["total"], 4)
        check_eq("重新加载索引 done 保持", idx2.get_stats(sk)["done"], 1)


# ────────── 4. OrgTypeCache 验证 ──────────
def test_org_cache():
    section("4. OrgTypeCache 验证")

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        cache = case_fetcher.OrgTypeCache(out)

        check("初始 get 返回 None", cache.get("某投资管理有限公司") is None)
        check("缓存文件初始不存在", not (out / "_org_type_cache.json").exists())

        cache.set("某投资管理有限公司", "私募证券投资基金管理人")
        check_eq(
            "set 后 get 返回正确类型",
            cache.get("某投资管理有限公司"),
            "私募证券投资基金管理人",
        )
        check("缓存文件已落盘", (out / "_org_type_cache.json").exists())

        # 重新加载
        cache2 = case_fetcher.OrgTypeCache(out)
        check_eq(
            "重新加载缓存命中",
            cache2.get("某投资管理有限公司"),
            "私募证券投资基金管理人",
        )

        # 覆盖更新
        cache2.set("某投资管理有限公司", "私募股权、创业投资基金管理人")
        check_eq(
            "缓存可覆盖更新",
            cache2.get("某投资管理有限公司"),
            "私募股权、创业投资基金管理人",
        )


# ────────── 5. 数据模型字段验证 ──────────
def test_case_data_model():
    section("5. CaseData 数据模型验证")
    fields = list(case_fetcher.CaseData.__dataclass_fields__.keys())
    required = [
        "case_id", "source_url", "source_type", "case_type", "bureau",
        "title", "date", "raw_text", "fetch_time", "ocr_success",
        "error", "pdf_url", "punished_entity", "entity_type",
        "is_private_fund", "pf_evidence", "org_type",
    ]
    for f in required:
        check(f"CaseData 含字段 {f}", f in fields)
    check_eq("CaseData 字段总数 == 17", len(fields), 17)


# ────────── 主入口 ──────────
def main():
    print("=" * 60)
    print("证监会私募基金数据库 - 冒烟测试")
    print("=" * 60)

    tests = [
        test_bureaus,
        test_utils,
        test_case_index,
        test_org_cache,
        test_case_data_model,
    ]
    for t in tests:
        try:
            t()
        except Exception:
            global FAIL
            FAIL += 1
            print(f"  [ERROR] {t.__name__} 抛出异常：")
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"测试汇总：PASS={PASS}  FAIL={FAIL}  SKIP={SKIP}")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

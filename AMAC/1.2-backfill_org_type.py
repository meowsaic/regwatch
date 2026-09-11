"""
为已有案例JSON补充 org_type（机构登记类型）和 punished_entity（受处分机构完整名称）字段
用法:
  python backfill_org_type.py              # 自动补全（API查询），失败后提示交互
  python backfill_org_type.py --dry-run    # 仅统计，不写入文件
  python backfill_org_type.py --interactive # 直接进入交互式终端补全
"""

import json
import re
import time
import logging
import argparse
import requests
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.resolve()
CASES_DIR = SCRIPT_DIR / "cases"


def load_case_fetcher_module():
    spec = spec_from_file_location("case_fetcher", SCRIPT_DIR / "1-case_fetcher.py")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def backfill(dry_run=False):
    cf = load_case_fetcher_module()
    cache = cf.OrgTypeCache(CASES_DIR)

    json_files = sorted((CASES_DIR / "institution").glob("*.json"))
    json_files = [f for f in json_files if not f.name.startswith("_")]

    total = len(json_files)
    already_has = 0
    filled = 0
    failed = 0
    entity_filled = 0
    failed_list = []

    logger.info("扫描到 %d 个机构类案例JSON", total)

    for i, jf in enumerate(json_files, 1):
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("[%d/%d] 读取失败 %s: %s", i, total, jf.name, e)
            continue

        if data.get("org_type"):
            already_has += 1
            continue
       

        title = data.get("title", "")
        raw_text = data.get("raw_text", "")
        case_id = data.get("case_id", jf.stem)

        punished_entity = data.get("punished_entity", "")
        if not punished_entity:
            punished_entity = cf.extract_punished_entity(title, raw_text, "Institution")
            if punished_entity:
                entity_filled += 1
                logger.info("[%d/%d] %s 机构名称 → %s", i, total, case_id, punished_entity)

        org_type = cf.resolve_org_type(title, raw_text, "Institution", cache, punished_entity)

        if org_type:
            filled += 1
            logger.info(
                "[%d/%d] %s → %s", i, total, case_id, org_type,
            )
            if not dry_run:
                data["org_type"] = org_type
                if punished_entity:
                    data["punished_entity"] = punished_entity
                with open(jf, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            failed += 1
            logger.warning(
                "[%d/%d] %s → 无法获取机构类型 (title=%s)",
                i, total, case_id, title[:40],
            )
            failed_list.append({
                "case_id": case_id,
                "title": title,
                "punished_entity": punished_entity,
                "file": str(jf.relative_to(SCRIPT_DIR)),
            })
            if not dry_run and punished_entity:
                data["punished_entity"] = punished_entity
                with open(jf, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

        time.sleep(1.5)

    logger.info("=" * 50)
    logger.info("补全完成")
    logger.info("  总计: %d", total)
    logger.info("  已有org_type: %d", already_has)
    logger.info("  本次补充org_type: %d", filled)
    logger.info("  本次补充punished_entity: %d", entity_filled)
    logger.info("  未获取到org_type: %d", failed)
    if dry_run:
        logger.info("  [DRY-RUN] 未写入文件")

    if failed_list:
        manual_path = CASES_DIR / "_org_type_manual.json"
        with open(manual_path, "w", encoding="utf-8") as f:
            json.dump(failed_list, f, ensure_ascii=False, indent=2)
        logger.info("  需人工补全清单: %s (%d 条)", manual_path, len(failed_list))

        if not dry_run:
            try:
                answer = input("\n是否进入交互式补全模式？(y/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer in ("y", "yes"):
                interactive_fill()

    return failed_list


def _extract_org_type_from_url(url: str) -> str:
    """从已注销管理人详情页URL自动提取机构类型"""
    try:
        resp = requests.get(
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
        if resp.status_code != 200:
            print(f"  ✗ 页面获取失败 (status={resp.status_code})")
            return ""

        resp.encoding = "utf-8"
        m = re.search(r"机构类型.*?<td[^>]*>(.*?)</td>", resp.text, re.S)
        if m:
            org_type = m.group(1).strip()
            if org_type:
                return org_type

        print("  ✗ 页面中未找到'机构类型'字段")
        return ""
    except Exception as e:
        print(f"  ✗ 请求失败: {e}")
        return ""


def interactive_fill():
    """交互式终端补全缺失的机构类型"""
    cf = load_case_fetcher_module()
    cache = cf.OrgTypeCache(CASES_DIR)

    json_files = sorted((CASES_DIR / "institution").glob("*.json"))
    json_files = [f for f in json_files if not f.name.startswith("_")]

    missing = []
    for jf in json_files:
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        if data.get("org_type"):
            continue

        missing.append((jf, data))

    if not missing:
        print("\n所有机构类案例均已拥有 org_type，无需补全。")
        return

    print(f"\n发现 {len(missing)} 个案例缺少 org_type，开始交互式补全")
    print("  输入编号选择类型 | 直接输入自定义类型 | 粘贴详情页URL自动提取 | 回车跳过 | q 退出\n")

    for idx, vt in enumerate(cf.ORG_TYPE_VALUES, 1):
        print(f"  {idx}. {vt}")
    print()

    filled = 0
    skipped = 0

    for i, (jf, data) in enumerate(missing, 1):
        title = data.get("title", "")
        punished_entity = data.get("punished_entity", "")
        case_id = data.get("case_id", jf.stem)

        org_display = punished_entity or "(无机构名称)"

        print(f"\n{'─' * 50}")
        print(f"  [{i}/{len(missing)}] 案例: {case_id}")
        print(f"  机构: {org_display}")
        print(f"  标题: {title}")

        while True:
            try:
                choice = input("  机构类型 (编号/自定义/URL/回车跳过/q退出): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\n退出交互模式。")
                _print_interactive_summary(filled, skipped)
                return

            if choice.lower() == "q":
                print()
                _print_interactive_summary(filled, skipped)
                return

            if choice == "":
                skipped += 1
                print("  → 跳过")
                break

            org_type = None

            if choice.startswith("http"):
                org_type = _extract_org_type_from_url(choice)
                if not org_type:
                    continue
                print(f"  → 从URL提取: {org_type}")
            elif choice.isdigit():
                num = int(choice)
                if 1 <= num <= len(cf.ORG_TYPE_VALUES):
                    org_type = cf.ORG_TYPE_VALUES[num - 1]
                else:
                    print(f"  ✗ 无效编号，请输入 1-{len(cf.ORG_TYPE_VALUES)}")
                    continue
            else:
                org_type = choice

            data["org_type"] = org_type
            with open(jf, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            if punished_entity:
                cache.set(punished_entity, org_type)

            filled += 1
            print(f"  ✓ 已保存: {org_type}")
            break

    print()
    _print_interactive_summary(filled, skipped)


def _print_interactive_summary(filled: int, skipped: int):
    print(f"交互式补全结束。已补全 {filled} 条，跳过 {skipped} 条。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="为已有案例JSON补充机构登记类型和受处分机构完整名称")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写入文件")
    parser.add_argument("--interactive", action="store_true", help="直接进入交互式终端补全模式")
    args = parser.parse_args()

    if args.interactive:
        interactive_fill()
    else:
        backfill(dry_run=args.dry_run)

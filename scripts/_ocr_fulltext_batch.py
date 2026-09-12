"""批量视觉 OCR 补全扫描 PDF 全文（可断点续跑）。

策略：
1. 优先处理正文最短的 AMAC PDF 案例
2. 下载 PDF → pypdfium2 渲染页图 → 多图视觉识别
3. 仅当新文本明显更长时覆盖 raw_text
4. 进度写入 data/ocr_cache/progress.json
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path

from regwatch.domain import Dataset
from regwatch.services import get_services
from regwatch.sources.http import close_shared_session, shared_session

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "ocr_cache"
PROGRESS = CACHE / "progress.json"
WORKLIST = CACHE / "worklist.json"
MIMO_PYTHON = os.environ.get("MIMO_PYTHON") or ""

PAGE_PROMPT = (
    "请完整逐字识别图中监管文书全文，保持段落顺序与结构，"
    "包括标题、文号、当事人、事实、决定、落款日期。不要总结、不要省略。"
)

MIN_GAIN = 120
TARGET_LEN = 4500


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_worklist(services) -> list[dict]:
    if WORKLIST.exists():
        return load_json(WORKLIST, [])
    # 回退：按短正文生成
    rows = services.store.db.query(
        """
        SELECT c.case_id, c.source_url, c.pdf_url, c.source_type, c.title,
               LENGTH(b.raw_text) AS n
        FROM cases c
        JOIN case_bodies b ON b.dataset=c.dataset AND b.case_id=c.case_id
        WHERE c.dataset='amac' AND c.status='done'
          AND LENGTH(b.raw_text) < ?
        ORDER BY LENGTH(b.raw_text) ASC, c.case_id ASC
        """,
        (TARGET_LEN,),
    )
    items = []
    for row in rows:
        pdf = (row["pdf_url"] or row["source_url"] or "").strip()
        if ".pdf" not in pdf.lower():
            continue
        items.append(
            {
                "case_id": str(row["case_id"]),
                "pdf_url": pdf,
                "title": str(row["title"] or ""),
                "old_len": int(row["n"] or 0),
            }
        )
    save_json(WORKLIST, items)
    return items


def download_pdf(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 2000:
        return True
    try:
        resp = shared_session().get(url, timeout=60)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return dest.stat().st_size > 2000
    except Exception:
        return False


def render_pages(pdf: Path, outdir: Path) -> list[Path]:
    if not MIMO_PYTHON:
        return []
    script = ROOT / "scripts" / "_render_pdf.py"
    try:
        proc = subprocess.run(
            [MIMO_PYTHON, str(script), str(pdf), str(outdir), "--scale", "1.4", "--max-pages", "8"],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    return sorted(outdir.glob("page_*.png"))


def vision_pages(services, pages: list[Path]) -> str:
    """多页图一次性识别。"""
    client = services.llm.client(task="vision")
    content: list[dict] = []
    for p in pages:
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            }
        )
    content.append({"type": "text", "text": PAGE_PROMPT})
    messages = [{"role": "user", "content": content}]
    result = client.chat(
        messages,
        model=client.vision_model,
        max_tokens=16000,
        temperature=0.1,
    )
    return (result.best_text() or "").strip()


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def looks_like_decision(text: str) -> bool:
    keys = (
        "作出如下",
        "决定如下",
        "给予警告",
        "公开谴责",
        "撤销管理人",
        "取消会员",
        "加入黑名单",
        "出具警示函",
        "责令改正",
        "本纪律处分",
        "特此决定",
    )
    return any(k in text for k in keys)


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    services = get_services()
    worklist = build_worklist(services)
    progress = load_json(PROGRESS, {"done": {}, "failed": {}})
    done: dict = progress.get("done") or {}
    failed: dict = progress.get("failed") or {}

    pending = [w for w in worklist if w["case_id"] not in done]
    print(f"worklist={len(worklist)} pending={len(pending)} done={len(done)}")

    # 本轮最多处理数量（可由环境变量覆盖）
    limit = int(os.environ.get("OCR_BATCH_LIMIT", "40"))
    processed = 0
    improved = 0
    errors = 0

    try:
        for item in pending:
            if processed >= limit:
                break
            cid = item["case_id"]
            pdf_url = item["pdf_url"]
            old_len = int(item.get("old_len") or 0)
            # 若库内正文已很长则跳过
            body = services.cases.get_body(Dataset.AMAC, cid) or ""
            title = item.get("title") or ""
            # 短文书若已是完整落款/送达公告，无需重扫
            if len(body) >= 800 and any(k in title for k in ("送达", "事先告知")):
                if re.search(r"(日|抄送)\s*$", body[-80:]):
                    done[cid] = {"status": "skipped_notice", "len": len(body)}
                    continue
            if len(body) >= TARGET_LEN and looks_like_decision(body):
                done[cid] = {"status": "already_ok", "len": len(body)}
                continue

            pdf_path = CACHE / f"{cid}.pdf"
            page_dir = CACHE / f"{cid}_pages"
            if not download_pdf(pdf_url, pdf_path):
                errors += 1
                failed[cid] = {"error": "download_failed"}
                print(f"[skip] download fail {cid}")
                time.sleep(0.5)
                continue

            pages = render_pages(pdf_path, page_dir)
            if not pages:
                errors += 1
                failed[cid] = {"error": "render_failed"}
                print(f"[skip] render fail {cid}")
                continue

            text = ""
            last_err = ""
            for attempt in range(4):
                try:
                    text = clean_text(vision_pages(services, pages))
                    if text:
                        break
                except Exception as exc:  # noqa: BLE001
                    last_err = type(exc).__name__
                    wait = 15 * (attempt + 1)
                    print(f"[retry] {cid} attempt {attempt + 1} {last_err} sleep {wait}s")
                    time.sleep(wait)
            processed += 1

            if len(text) < 80:
                errors += 1
                failed[cid] = {"error": last_err or "empty_ocr", "len": len(text)}
                print(f"[fail] {cid} ocr empty ({last_err})")
                time.sleep(5)
                continue

            cur = services.cases.get_body(Dataset.AMAC, cid) or ""
            if len(text) > len(cur) + MIN_GAIN:
                services.store.cases.set_body(Dataset.AMAC, cid, text)
                improved += 1
                print(f"[ok] {cid} {len(cur)}→{len(text)} pages={len(pages)}")
                done[cid] = {"status": "improved", "len": len(text), "pages": len(pages)}
            else:
                print(f"[keep] {cid} cur={len(cur)} new={len(text)} pages={len(pages)}")
                done[cid] = {"status": "kept", "len": len(cur), "new_len": len(text)}

            # 定期落盘进度
            if processed % 3 == 0:
                save_json(PROGRESS, {"done": done, "failed": failed})
                services.store.touch()
            time.sleep(8.0)
    finally:
        save_json(PROGRESS, {"done": done, "failed": failed})
        try:
            close_shared_session()
        except Exception:
            pass
        services.store.touch()

    print(
        f"BATCH_END processed={processed} improved={improved} errors={errors} "
        f"done_total={len(done)} pending_left={len(worklist) - len(done)}"
    )


if __name__ == "__main__":
    main()

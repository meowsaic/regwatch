"""用 pypdfium2 把 PDF 渲染为 PNG 页图（供视觉 OCR）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    parser.add_argument("outdir")
    parser.add_argument("--scale", type=float, default=1.5)
    parser.add_argument("--max-pages", type=int, default=12)
    args = parser.parse_args()

    try:
        import pypdfium2 as pdfium
    except ImportError:
        print("NO_PYPDFIUM2", file=sys.stderr)
        return 2

    pdf_path = Path(args.pdf)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("page_*.png"):
        old.unlink()

    doc = pdfium.PdfDocument(str(pdf_path))
    n = min(len(doc), args.max_pages)
    for i in range(n):
        page = doc[i]
        bitmap = page.render(scale=args.scale)
        pil = bitmap.to_pil()
        out = outdir / f"page_{i + 1:02d}.png"
        pil.save(out, format="PNG", optimize=True)
        page.close()
    doc.close()
    print(f"RENDERED {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

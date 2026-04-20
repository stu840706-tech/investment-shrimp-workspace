#!/usr/bin/env python3
"""extract_pdfplumber.py — 用 pdfplumber 從 PDF 抽文字

適用:有文字層的 PDF(一般券商報告、新聞稿、財報)。

用法:
    供 pdf_dispatch.py import
    或獨立執行:python3 extract_pdfplumber.py <pdf_path>

回傳(extract_with_pdfplumber):
    (text: str, page_count: int)
"""
import sys
from pathlib import Path


def extract_with_pdfplumber(pdf_path: Path):
    """抽出 PDF 所有頁文字,回傳 (full_text, page_count)。"""
    import pdfplumber

    pages_text = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        page_count = len(pdf.pages)
        for i, page in enumerate(pdf.pages, start=1):
            # x_tolerance/y_tolerance 預設值,B5 若發現多欄混讀可調整
            try:
                page_text = page.extract_text() or ""
            except Exception as e:
                print(f"[WARN] page {i} 抽取失敗: {e}", file=sys.stderr)
                page_text = ""
            pages_text.append(f"--- Page {i} ---\n{page_text}\n")

    return "\n".join(pages_text), page_count


def main():
    if len(sys.argv) < 2:
        print("用法: python3 extract_pdfplumber.py <pdf_path>", file=sys.stderr)
        return 2

    pdf_path = Path(sys.argv[1]).expanduser().resolve()
    if not pdf_path.exists():
        print(f"[ERROR] PDF 不存在: {pdf_path}", file=sys.stderr)
        return 2

    try:
        text, page_count = extract_with_pdfplumber(pdf_path)
        print(f"[INFO] {page_count} 頁, {len(text)} 字元", file=sys.stderr)
        sys.stdout.write(text)
        return 0
    except ImportError:
        print("[ERROR] pdfplumber 未安裝,請 pip install pdfplumber", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

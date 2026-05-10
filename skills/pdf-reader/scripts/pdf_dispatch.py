#!/usr/bin/env python3
"""pdf_dispatch.py — PDF 轉純文字主入口

先試 pdfplumber(快、適用文字層 PDF);失敗或輸出太少 → fallback OCR(tesseract)。

用法:
    python3 pdf_dispatch.py <pdf_path>

輸出:
    stdout: 純文字內容
    stderr: 使用方法、頁數、字元數
    exit 0: 成功
    exit 1: 兩路都失敗
    exit 2: PDF 路徑不存在
"""
import sys
from pathlib import Path

# 門檻:pdfplumber 輸出字元 <= 此值 → 視為可能是掃描件,fallback OCR
MIN_TEXT_CHARS = 500


def main():
    if len(sys.argv) < 2:
        print("用法: python3 pdf_dispatch.py <pdf_path>", file=sys.stderr)
        return 2

    pdf_path = Path(sys.argv[1]).expanduser().resolve()
    if not pdf_path.exists():
        print(f"[ERROR] PDF 不存在: {pdf_path}", file=sys.stderr)
        return 2
    if not pdf_path.is_file():
        print(f"[ERROR] 不是檔案: {pdf_path}", file=sys.stderr)
        return 2

    # 把 scripts/ 加進 sys.path 讓 extract_* 可被 import
    script_dir = Path(__file__).parent.resolve()
    sys.path.insert(0, str(script_dir))

    # Step 1: 試 pdfplumber
    print(f"[INFO] 嘗試 pdfplumber: {pdf_path.name}", file=sys.stderr)
    try:
        from extract_pdfplumber import extract_with_pdfplumber  # noqa
        text, page_count = extract_with_pdfplumber(pdf_path)
        char_count = len(text.strip())
        print(
            f"[INFO] pdfplumber 結果: {page_count} 頁, {char_count} 字元",
            file=sys.stderr,
        )
        if char_count > MIN_TEXT_CHARS:
            print(f"[METHOD] pdfplumber", file=sys.stderr)
            sys.stdout.write(text)
            return 0
        else:
            print(
                f"[INFO] pdfplumber 字元數 {char_count} <= {MIN_TEXT_CHARS},疑似掃描件,改走 OCR",
                file=sys.stderr,
            )
    except ImportError as e:
        print(f"[WARN] pdfplumber 套件未安裝: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] pdfplumber 失敗: {e},改走 OCR", file=sys.stderr)

    # Step 2: fallback OCR
    print(f"[INFO] 嘗試 OCR(tesseract chi_tra+eng)", file=sys.stderr)
    try:
        from extract_ocr import extract_with_ocr  # noqa
        text, page_count = extract_with_ocr(pdf_path)
        char_count = len(text.strip())
        print(
            f"[INFO] OCR 結果: {page_count} 頁, {char_count} 字元",
            file=sys.stderr,
        )
        if char_count > 0:
            print(f"[METHOD] ocr", file=sys.stderr)
            sys.stdout.write(text)
            return 0
        else:
            print(f"[ERROR] OCR 輸出為空", file=sys.stderr)
            return 1
    except ImportError as e:
        print(
            f"[ERROR] OCR 依賴未安裝: {e}\n"
            f"請執行: pip install pytesseract pdf2image Pillow && "
            f"sudo apt-get install -y tesseract-ocr tesseract-ocr-chi-tra tesseract-ocr-eng poppler-utils",
            file=sys.stderr,
        )
        return 1
    except Exception as e:
        print(f"[ERROR] OCR 失敗: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

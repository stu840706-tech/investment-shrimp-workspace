#!/usr/bin/env python3
"""extract_ocr.py — 用 tesseract OCR 從 PDF 抽文字

適用:掃描件、無文字層的 PDF。

流程:pdf2image 把每頁轉 PNG → pytesseract 對每頁 OCR(chi_tra+eng)→ 合併。

用法:
    供 pdf_dispatch.py import
    或獨立執行:python3 extract_ocr.py <pdf_path>

回傳(extract_with_ocr):
    (text: str, page_count: int)

依賴:
    系統:tesseract-ocr, tesseract-ocr-chi-tra, tesseract-ocr-eng, poppler-utils
    Python:pytesseract, pdf2image, Pillow
"""
import sys
from pathlib import Path

# DPI 愈高愈清晰但愈慢,200 是實務平衡點
OCR_DPI = 200
# 繁中 + 英文混合辨識
OCR_LANG = "chi_tra+eng"


def extract_with_ocr(pdf_path: Path, max_pages: int = 10, dpi: int = 150):
    """OCR PDF(最多 max_pages 頁,降低 DPI 加速),回傳 (full_text, page_count)。"""
    from pdf2image import convert_from_path
    import pytesseract

    try:
        images = convert_from_path(str(pdf_path), dpi=dpi, first_page=1, last_page=max_pages)
    except Exception as e:
        raise RuntimeError(f"pdf2image 轉圖失敗: {e}")

    page_count = len(images)
    pages_text = []
    for i, img in enumerate(images, start=1):
        try:
            page_text = pytesseract.image_to_string(img, lang=OCR_LANG)
        except pytesseract.TesseractNotFoundError:
            raise RuntimeError("tesseract 未安裝或不在 PATH。")
        except Exception as e:
            print(f"[WARN] page {i} OCR 失敗: {e}", file=sys.stderr)
            page_text = ""
        pages_text.append(f"--- Page {i} ---\n{page_text}\n")

    return "\n".join(pages_text), page_count


def main():
    if len(sys.argv) < 2:
        print("用法: python3 extract_ocr.py <pdf_path>", file=sys.stderr)
        return 2

    pdf_path = Path(sys.argv[1]).expanduser().resolve()
    if not pdf_path.exists():
        print(f"[ERROR] PDF 不存在: {pdf_path}", file=sys.stderr)
        return 2

    try:
        text, page_count = extract_with_ocr(pdf_path)
        print(f"[INFO] {page_count} 頁, {len(text)} 字元", file=sys.stderr)
        sys.stdout.write(text)
        return 0
    except ImportError as e:
        print(
            f"[ERROR] OCR 依賴未安裝: {e}\n"
            f"請執行: pip install pytesseract pdf2image Pillow",
            file=sys.stderr,
        )
        return 1
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

---
name: pdf-reader
description: 把 PDF 轉成純文字。先試 pdfplumber,失敗 fallback tesseract OCR(繁中+英文)。支援一般券商 PDF 和掃描件。
---

# pdf-reader — PDF 轉純文字

## 何時使用

- 收到任何 PDF 檔(券商報告、年報、財報、法說會簡報)
- 要把 PDF 內容送給 M2.7 分析前(M2.7 是純文字模型,不吃 PDF)
- **紅線**:M2.7 絕不直接處理 PDF,必須經過此 skill

## 輸入

- PDF 檔案絕對路徑(例:`/tmp/report.pdf`)

## 輸出

- stdout 印出純文字內容
- stderr 印出使用的方法(`pdfplumber` 或 `ocr`)、頁數、字元數
- return code:0 成功,1 失敗

## 執行

```bash
python3 skills/pdf-reader/scripts/pdf_dispatch.py <pdf_path>
```

## 內部流程

```
pdf_dispatch.py
├── 1. 嘗試 extract_pdfplumber.py
│     └── 成功且字元數 > 100 → 回傳結果,結束
├── 2. pdfplumber 失敗 / 字元數 <= 100(疑似掃描件)
│     └── 呼叫 extract_ocr.py
│         ├── pdf2image 轉成 PNG
│         ├── tesseract(chi_tra+eng)OCR
│         └── 合併各頁文字
└── 3. 兩路都失敗 → exit 1,stderr 印原因
```

## 依賴

系統層:

- `tesseract-ocr`(OCR 引擎)
- `tesseract-ocr-chi-tra`(繁中語言包)
- `tesseract-ocr-eng`(英文語言包)
- `poppler-utils`(pdf2image 後端)

Python 層:

- `pdfplumber`
- `pytesseract`
- `pdf2image`
- `Pillow`

B5 第一步會檢查並安裝:

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-chi-tra tesseract-ocr-eng poppler-utils
pip install pdfplumber pytesseract pdf2image Pillow
```

## 驗證(B5 Step 2)

Kai 從 Telegram 傳一份券商 PDF → OpenClaw 存到 `/tmp/<file>.pdf` → 執行:

```bash
python3 skills/pdf-reader/scripts/pdf_dispatch.py /tmp/<file>.pdf
```

**成功判定**:

- stdout 有可讀中文/英文內容
- stderr 標明用 `pdfplumber` 或 `ocr`
- 字元數 > 100(排除全空白、亂碼)

## 錯誤處理

- PDF 檔不存在 → exit 2
- pdfplumber 拋例外 → 走 OCR 分支
- OCR 也失敗(tesseract 沒安裝 / 繁中語言包沒裝 / PDF 整份空白)→ exit 1 + 明確錯誤訊息
- **絕不腦補內容**(P-002、P-009):失敗就失敗

## 常見陷阱

- **掃描件偽裝成文字 PDF**:pdfplumber 回傳空字串,這時要 fallback OCR(用字元數 <= 100 當門檻)
- **繁中語言包沒裝**:tesseract 預設只有英文,會輸出拼音式亂碼
- **多欄排版**:pdfplumber 有時會把左右欄混讀,需要 B5 實測後決定要不要調 `layout=True`
- **PDF 有密碼**:目前不處理,exit 並告知 Kai

# classification_examples.md — 券商材料分類 few-shot

_此檔提供給 `receive_telegram.py` 的 M2.7 分類 prompt 當 few-shot examples。B5 實測後累積。_

## 目的

M2.7 對券商三類(個股/產業/晨報)邊界 case 會分錯。Kai 糾正後把案例加進本檔,作為下次分類的 few-shot,讓分類器漸進改善。

## 格式

每個 example 一個 section:

```
### Example: <一句話描述>

**輸入文本(節錄)**:
<前 500 字節錄>

**正確分類**:stock_report / industry_report / morning_brief

**分類理由**:
<1-2 句話說明為什麼是這類,特別是邊界 case 的判斷依據>
```

## Examples

_(尚無 examples。B5 實測後 Kai 糾正分類時加入。)_

## 邊界 case 判斷準則(先給 M2.7 的通則,examples 累積再 override)

- **個股 vs 產業**:全文 >70% 篇幅聚焦單一公司 → 個股;橫跨多家公司 + 產業趨勢 → 產業
- **產業 vs 晨報**:晨報通常含當日大盤觀點 + 多檔 highlight(每檔篇幅短);產業報告會深挖一個 theme
- **個股 vs 晨報**:晨報提個股通常只 1-2 段,個股報告至少 1-2 頁的深度

---

_上次更新:2026-04-19(初始空檔)_

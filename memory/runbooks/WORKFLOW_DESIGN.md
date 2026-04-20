# WORKFLOW_DESIGN.md — 工作流設計原則

_AGENTS.md Session step 8 會查此檔。寫新 workflow 或 skill 前必讀。_

## 核心分工

**Python(workflows/)做什麼**

- cron 排程的精確時間任務
- 資料擷取(API 呼叫、PDF 解析、網頁抓取)
- 資料轉換(清洗、格式化、計算衍生欄位)
- 寫入 Notion / state / log
- 純規則邏輯(閾值判斷、去重、排序)

**AI(M2.7 via OpenClaw)做什麼**

- 文本理解(新聞摘要、事件萃取)
- 分類(個股/產業/晨報、signal 高低)
- 定性判斷(「這則新聞是事實還是觀點?」)
- 跨段推理(「合併這 5 則報導得出什麼?」)

**鐵律**:Python 做協調與資料,AI 只做純分析。AI 不當 orchestrator。

## workflow 骨架模板

```python
#!/usr/bin/env python3
"""<workflow 名稱> — <一句話用途>"""
import json, time, os, sys
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw" / "workspace"
STATE_FILE = WORKSPACE / "state" / "<workflow>_state.json"
SECRETS_FILE = WORKSPACE / "config" / "secrets.json"

def load_state():
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))

def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def main():
    state = load_state()
    try:
        # ... 實際工作
        save_state(state)
    except Exception as e:
        # escalate,不腦補
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
```

## Skill 骨架模板

Skill 目錄結構:

```
skills/<skill-name>/
├── SKILL.md          # 必有,description < 100 字元會進 system prompt
├── scripts/          # 實際執行程式
│   └── <action>.py
└── references/       # 選用,範例/查表/提示詞
    └── examples.md
```

`SKILL.md` 的 description 是 agent 判斷是否載入此 skill 的依據,要寫得**具體、動詞開頭、說明何時該用**。

## 補救狀態機(所有 workflow 通用)

API 失敗或資料缺漏時的處理順序:

1. **pending** — 標註缺失(`[Data Missing]`),嘗試補救腳本(備用 API、cached 資料)
2. **retry** — 僅限網路錯誤(timeout、5xx),最多 1 次,`time.sleep(5)` 後重試
3. **escalate** — 補救仍失敗,Telegram 通知 Kai,**不腦補、不填空**
4. **halt** — 連續 3 次失敗 or 兩來源矛盾 >50%,停止該項目分析並寫 state

永遠不要為了「讓 workflow 看起來成功」捏造數字。

## API 額度紀律

- FinMind 每日 600 次上限,到 590 次停止(留 10 次緩衝)
- 統一用 `workflows/_api_manager.py`(B6 建立)做 rate limit
- 同一個 API 不可被多個 workflow 同時高頻呼叫
- 額度耗盡 → state 記錄「等重置」,下一個 cron 週期恢復

## thinking=on / thinking=off 規則

依 P-009:

- **預設 thinking=off**:事實萃取、資料庫寫入、對外發送(Telegram/Notion/Email)
- **thinking=on 才開**:多輪 tool use、複雜推理、subagent 協調
- thinking=on 輸出必須**比對原文檢查幻覺**。T2 測試已驗證 thinking=on 會捏造原文沒有的數字

## 新增 workflow / skill 的流程

1. 先問:這件事該是 skill(AI 判斷觸發)還是 workflow(時間觸發)?
2. 寫骨架,先讓 `--dry-run` 可跑
3. 單元測試:手動給 input,檢查 output 合理
4. 接 cron 或 Telegram listener
5. 觀察 1-3 天,檢查異常狀態
6. 寫入 `memory/runbooks/` 對應的 how-to
7. 依 GP-015 commit

## 常見陷阱

- **讓 AI 當 orchestrator**:context 會爆、decision 會飄;Python 做控制流
- **全部資料塞進 LLM prompt**:M2.7 實務甜蜜點 30-50K tokens,超過 80K 效能退化
- **忘記 state 寫入**:workflow 中斷後無法續跑 → P-008 要求寫磁碟
- **多個 workflow 共用 state 檔**:造成競爭;每個 workflow 獨立 state
- **LLM 輸出直接寫 Notion**:沒經過驗證,幻覺會上資料庫;中間要有 Python 檢查層

---

_上次更新:2026-04-19_

#!/usr/bin/env python3
content = open('AGENTS.md').read()

old = """**不觸發 commit 的情況**:

- Heartbeat 檢查的日誌(太頻繁,只寫檔不 commit)"""

new = """**不觸發 commit 的情況**:

- Kai 未明確說「commit」或「做完了」時，不自行 commit
- 執行診斷指令後發現問題，不自行修復後 commit，必須先回報 Kai 等待指令
- Heartbeat 檢查的日誌(太頻繁,只寫檔不 commit)"""

if old in content:
    content = content.replace(old, new, 1)
    open('AGENTS.md', 'w').write(content)
    print('OK')
else:
    print('ERROR: target not found')
    idx = content.find('不觸發 commit')
    if idx >= 0:
        print(repr(content[idx:idx+200]))

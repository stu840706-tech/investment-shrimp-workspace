#!/bin/bash
# daily_scan_pipeline.sh - 每日掃描三段式流程
# 執行順序：daily-scan -> daily-notion -> daily-scan-summary
# 任一步失敗即中止，不繼續執行後續步驟

set -e
WORKFLOWS="$HOME/.openclaw/workspace/workflows"
LOG_DIR="$HOME/.openclaw/workspace/logs"
mkdir -p "$LOG_DIR"

DATE_TAG=$(date +%Y%m%d)
LOG_FILE="$LOG_DIR/daily_scan_pipeline_${DATE_TAG}.log"

echo "========================================" | tee -a "$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S UTC')] pipeline 開始" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

echo "[Step 1] daily-scan.py" | tee -a "$LOG_FILE"
cd "$WORKFLOWS"
python3 daily-scan.py 2>&1 | tee -a "$LOG_FILE"
echo "[Step 1] 完成" | tee -a "$LOG_FILE"

echo "[Step 2] daily-notion.py" | tee -a "$LOG_FILE"
python3 daily-notion.py 2>&1 | tee -a "$LOG_FILE"
echo "[Step 2] 完成" | tee -a "$LOG_FILE"

echo "[Step 3] daily-scan-summary.py" | tee -a "$LOG_FILE"
python3 daily-scan-summary.py 2>&1 | tee -a "$LOG_FILE"
echo "[Step 3] 完成" | tee -a "$LOG_FILE"

echo "[Step 4] daily_dashboard.py" | tee -a "$LOG_FILE"
python3 daily_dashboard.py 2>&1 | tee -a "$LOG_FILE"
echo "[Step 4] 完成" | tee -a "$LOG_FILE"

echo "========================================" | tee -a "$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S UTC')] pipeline 全部完成" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

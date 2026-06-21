#!/usr/bin/env bash
set -u
LOG="$HOME/.openclaw/cloudflared.log"
RECV="$HOME/.openclaw/line_receiver.py"
LINE_TOKEN=$(grep '^LINE_TOKEN' "$RECV" | head -1 | cut -d'"' -f2)
: > "$LOG"
"$HOME/cloudflared" tunnel --url http://127.0.0.1:5000 --no-autoupdate > "$LOG" 2>&1 &
CF_PID=$!
URL=""
for i in $(seq 1 30); do
 URL=$(grep -oE 'https://[-a-z0-9]+.trycloudflare.com' "$LOG" | head -1)
 [ -n "$URL" ] && break
 sleep 1
done
if [ -z "$URL" ]; then
 echo "no tunnel url"; kill "$CF_PID" 2>/dev/null; exit 1
fi
for i in $(seq 1 5); do
 CODE=$(curl -s -o /dev/null -w '%{http_code}' -X PUT https://api.line.me/v2/bot/channel/webhook/endpoint -H "Authorization: Bearer $LINE_TOKEN" -H 'Content-Type: application/json' -d '{"endpoint":"'"$URL"'/line"}')
 echo "$(date '+%F %T') webhook=$URL/line try=$i code=$CODE"
 [ "$CODE" = "200" ] && break
 sleep 5
done
wait "$CF_PID"

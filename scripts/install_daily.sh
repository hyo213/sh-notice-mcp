#!/bin/bash
# 매일 아침 8:31에 sh-notice-agent --daily 를 실행하도록 macOS launchd에 등록한다.
# 맥이 잠자고 있었으면 깨어난 뒤 한 번 실행된다. 해제: scripts/uninstall_daily.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENT="$DIR/.venv/bin/sh-notice-agent"
PLIST="$HOME/Library/LaunchAgents/com.sh-notice.agent.plist"
LOGS="$HOME/.sh-notice-mcp/logs"
[ -x "$AGENT" ] || { echo "먼저 설치하세요: .venv/bin/pip install -e '.[agent]'"; exit 1; }
security find-generic-password -s sh-notice-anthropic -w >/dev/null 2>&1 || \
  echo "주의: 키체인에 API 키가 없습니다. security add-generic-password -s sh-notice-anthropic -a \"$USER\" -w"
mkdir -p "$HOME/Library/LaunchAgents" "$LOGS"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.sh-notice.agent</string>
  <key>ProgramArguments</key><array><string>$AGENT</string><string>--daily</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>31</integer></dict>
  <key>StandardOutPath</key><string>$LOGS/agent.out.log</string>
  <key>StandardErrorPath</key><string>$LOGS/agent.err.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "등록 완료: 매일 08:31. 지금 바로 시험하려면: launchctl kickstart gui/$(id -u)/com.sh-notice.agent"
echo "로그: $LOGS/agent.err.log"

#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.sh-notice.agent.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
rm -f "$PLIST" && echo "매일 실행을 해제했습니다."

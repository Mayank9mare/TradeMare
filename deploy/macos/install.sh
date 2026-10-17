#!/usr/bin/env bash
# Install (or remove) a launchd agent that keeps `trademare serve` running on this Mac:
# it starts at login and restarts if it exits. Logs go to logs/serve.log.
#   ./deploy/macos/install.sh            install / update
#   ./deploy/macos/install.sh uninstall  remove
set -euo pipefail

LABEL="com.trademare.serve"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT="$(cd "$(dirname "$0")/../.." && pwd)"
UV="$(command -v uv || true)"

if [[ "${1:-}" == "uninstall" ]]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL"
  exit 0
fi

[[ -n "$UV" ]] || { echo "uv not found on PATH (brew install uv)"; exit 1; }
mkdir -p "$PROJECT/logs" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>ProgramArguments</key>
  <array>
    <string>$UV</string><string>run</string><string>trademare</string><string>serve</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$PROJECT/logs/serve.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/logs/serve.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$(dirname "$UV"):/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL: dashboard at http://127.0.0.1:8000, logs in $PROJECT/logs/serve.log"
echo "Tip: keep the Mac awake around 17:00 IST / 17:30 New York, e.g.:"
echo "  sudo pmset repeat wakeorpoweron MTWRF 16:55:00"

#!/bin/bash

# Ensure this script is run as root or with sudo for system commands
if [ "$EUID" -ne 0 ]; then
  echo "[WARNING] Some cleanup actions (like system journal and apt) require root privileges."
  echo "Please run as: sudo $0"
  exit 1
fi

echo "=================================================="
echo "          Starting VPS System Cleanup"
echo "=================================================="
INITIAL_SPACE=$(df -h / | awk 'NR==2 {print $3}')
echo "Initial used disk space: $INITIAL_SPACE"
echo "--------------------------------------------------"

# 1. Clean Google Chrome Cache (Safe - Keeps Logins)
echo "• Cleaning Google Chrome Cache & Crash Reports..."
CHROME_PROFILE_DIR="/home/ujjwal/src/IndexTimeMachine/ChromeProfile"
if [ -d "$CHROME_PROFILE_DIR" ]; then
    # Delete temporary cache files safely
    rm -rf "$CHROME_PROFILE_DIR"/Default/Cache/* 2>/dev/null
    rm -rf "$CHROME_PROFILE_DIR"/Default/Code\ Cache/* 2>/dev/null
    rm -rf "$CHROME_PROFILE_DIR"/Default/GPUCache/* 2>/dev/null
    rm -rf "$CHROME_PROFILE_DIR"/Default/Service\ Worker/CacheStorage/* 2>/dev/null
    rm -rf "$CHROME_PROFILE_DIR"/Crashpad/reports/* 2>/dev/null
    echo "  [DONE] Chrome caches cleared."
else
    echo "  [SKIP] Chrome profile directory not found."
fi

# 2. Clean VS Code Server Cached Binaries (Safe - Auto-downloads on next connect)
echo "• Cleaning VS Code CLI Server Cache..."
VSCODE_CLI_DIR="/home/ujjwal/.vscode-server/cli/servers"
if [ -d "$VSCODE_CLI_DIR" ]; then
    rm -rf "$VSCODE_CLI_DIR"/* 2>/dev/null
    echo "  [DONE] VS Code server cache cleared."
else
    echo "  [SKIP] VS Code CLI server directory not found."
fi

# 3. Clean up Scraper and Trade Logs (Older than 7 days)
echo "• Cleaning application logs older than 7 days..."
LOG_DIRS=(
    "/home/ujjwal/src/IndexTimeMachine/logs"
    "/home/ujjwal/src/training/logs"
)
for dir in "${LOG_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        find "$dir" -type f -name "*.log" -mtime +7 -delete 2>/dev/null
        find "$dir" -type f -name "*.txt" -mtime +7 -delete 2>/dev/null
        echo "  [DONE] Old logs cleared in: $dir"
    else
        echo "  [SKIP] Log directory not found: $dir"
    fi
done

# 4. Clean Systemd Journal Logs (Limits history to 100MB)
echo "• Vacuuming Systemd Journal Logs to 100MB..."
journalctl --vacuum-size=100M >/dev/null 2>&1
rm -rf /var/log/journal/* 2>/dev/null
rm -f /var/log/*.gz /var/log/*.1 2>/dev/null
echo "  [DONE] System logs vacuumed."

# 5. Clean Package Manager Cache (APT)
echo "• Cleaning APT package cache and autoremoving packages..."
apt-get clean -y >/dev/null 2>&1
apt-get autoremove --purge -y >/dev/null 2>&1
echo "  [DONE] Package cache cleared."

# 6. Docker Cleanup (Optional / Safe prune)
# Wipes stopped containers, dangling images, and unused networks to prevent background creep.
if command -v docker &> /dev/null; then
    echo "• Pruning unused Docker resources..."
    docker system prune -f >/dev/null 2>&1
    docker volume prune -f >/dev/null 2>&1
    echo "  [DONE] Docker system pruned."
fi

echo "--------------------------------------------------"
FINAL_SPACE=$(df -h / | awk 'NR==2 {print $3}')
echo "Final used disk space: $FINAL_SPACE"
echo "=================================================="
echo "[SUCCESS] Cleanup complete!"
echo "=================================================="

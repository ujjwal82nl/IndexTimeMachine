#!/bin/bash

umask 002

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=================================================="
echo " Stopping TradingView Scraper & Executor Environment"
echo "=================================================="

# Stop the python trade executors
echo "• Stopping trade executors..."
pkill -f trade_executor.py 2>/dev/null

# Stop the python scraper
echo "• Stopping scraper script..."
pkill -f tv_scraper.py 2>/dev/null

# Stop Google Chrome
echo "• Stopping Google Chrome..."
pkill -f google-chrome 2>/dev/null
pkill -f chrome 2>/dev/null

# Stop the virtual framebuffer display server
echo "• Stopping Xvfb..."
pkill -f Xvfb 2>/dev/null
pkill -f xvfb-run 2>/dev/null

# Detect and activate Python virtual environment if present for report generation
if [ -d "$SCRIPT_DIR/.venv" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
elif [ -d "$SCRIPT_DIR/venv" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
elif [ -d "$SCRIPT_DIR/venv_314" ]; then
    if [ -f "$SCRIPT_DIR/venv_314/bin/activate" ]; then
        source "$SCRIPT_DIR/venv_314/bin/activate"
    fi
fi

# Generate end-of-day reports
echo "• Generating trade reports..."
if [ -f "$SCRIPT_DIR/reports/generate_report.py" ]; then
    python3 "$SCRIPT_DIR/reports/generate_report.py"
else
    echo "[WARNING] reports/generate_report.py not found. Skipping report generation."
fi

echo "=================================================="
echo "[SUCCESS] All processes stopped and reports updated successfully."
echo "=================================================="

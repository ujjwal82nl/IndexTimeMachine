#!/bin/bash

umask 002

# Configuration
PORT=9223
CHROME_PROFILE_DIR="./ChromeProfile"
SCRAPER_SCRIPT="tv_scraper.py"

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo " Starting TradingView Scraper Environment (Linux)"
echo "=================================================="

# Function to check if a port is open
is_port_open() {
    (echo > /dev/tcp/127.0.0.1/$1) >/dev/null 2>&1
}

# Check if port is active
if is_port_open $PORT; then
    echo "[INFO] Chrome debugging instance already active on port $PORT. Skipping launch."
else
    echo "1. Starting Chrome inside Xvfb in Remote Debugging Mode..."
    
    # Run Chrome in the background with Xvfb
    # Using security and resource optimization flags suited for VPS/Server environments
    xvfb-run -a --server-args="-screen 0 1920x1080x24" google-chrome \
        --remote-debugging-port=$PORT \
        --remote-allow-origins="*" \
        --user-data-dir="$SCRIPT_DIR/$CHROME_PROFILE_DIR" \
        --no-first-run \
        --no-default-browser-check \
        --disable-search-engine-choice-screen \
        --disable-sync \
        --no-sandbox \
        --disable-gpu \
        --disable-dev-shm-usage > "$SCRIPT_DIR/chrome_err.log" 2>&1 &
        
    # Wait for Chrome to initialize
    sleep 3
    
    if is_port_open $PORT; then
        echo "[SUCCESS] Chrome launched successfully on port $PORT."
    else
        echo "[ERROR] Chrome failed to start. Check $SCRIPT_DIR/chrome_err.log for details."
        exit 1
    fi
fi

echo ""
echo "=================================================="
echo " ACTION REQUIRED (Only if first run or session expired):"
echo " 1. Create an SSH tunnel from your local PC:"
echo "    ssh -L $PORT:127.0.0.1:$PORT user@your-vps-ip"
echo " 2. Open chrome://inspect or edge://inspect on your PC"
echo " 3. Verify that the TradingView chart is open and logged in."
echo "=================================================="
#read -p "Press [Enter] to start the scraper script..."
echo ""

# Detect and activate Python virtual environment if present
if [ -d "$SCRIPT_DIR/.venv" ]; then
    echo "[INFO] Activating virtual environment: .venv"
    source "$SCRIPT_DIR/.venv/bin/activate"
elif [ -d "$SCRIPT_DIR/venv" ]; then
    echo "[INFO] Activating virtual environment: venv"
    source "$SCRIPT_DIR/venv/bin/activate"
elif [ -d "$SCRIPT_DIR/venv_314" ]; then
    # In case of Windows venv copied to Linux, check for standard Linux bin/activate
    if [ -f "$SCRIPT_DIR/venv_314/bin/activate" ]; then
        echo "[INFO] Activating virtual environment: venv_314"
        source "$SCRIPT_DIR/venv_314/bin/activate"
    fi
fi

# Run the python scraper
echo "3. Launching Python Scraper Script..."
export PYTHONIOENCODING="utf-8"
python3 "$SCRIPT_DIR/$SCRAPER_SCRIPT"

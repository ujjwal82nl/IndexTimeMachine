"""
telegram_bot.py
---------------
Multi-bot Telegram daemon for IndexTimeMachine VPS control.

Each bot is defined in scripts/bots_config.json with a unique name and role.
The role determines which command handler set is loaded.

Usage:
    python3 scripts/telegram_bot.py <bot_name>

    e.g.
    python3 scripts/telegram_bot.py uk_timemachinebot
    python3 scripts/telegram_bot.py uk_dhanalgobot       # when ready

Roles implemented:
    timemachine  —  CHECK, START, STOP, HELP
    dhan_algo    —  (placeholder, future)

Security:
    Only responds to the chat_id configured for that specific bot.
    All other senders are silently ignored and logged.

Run as a daemon (manual):
    nohup python3 scripts/telegram_bot.py uk_timemachinebot \
        >> logs/bot_uk_timemachinebot.log 2>&1 &

Run as a systemd service:
    See scripts/trading-bot-uk_timemachinebot.service
"""

import os
import sys
import json
import time
import subprocess
import urllib.request
from datetime import datetime

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR    = os.path.dirname(SCRIPTS_DIR)          # IndexTimeMachine/
LOGS_DIR    = os.path.join(BASE_DIR, "logs")
TRADES_DIR  = os.path.join(BASE_DIR, "trades")
CONFIG_FILE = os.path.join(SCRIPTS_DIR, "bots_config.json")

os.makedirs(LOGS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Parse bot name from CLI
# ---------------------------------------------------------------------------
if len(sys.argv) < 2:
    print("Usage: python3 scripts/telegram_bot.py <bot_name>")
    print("Available bots are defined in scripts/bots_config.json")
    sys.exit(1)

BOT_NAME = sys.argv[1].strip()

# ---------------------------------------------------------------------------
# Load bots_config.json
# ---------------------------------------------------------------------------
def load_bots_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        print(f"[ERROR] Config file not found: {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


bots_config = load_bots_config()
all_bots    = bots_config.get("bots", {})

if BOT_NAME not in all_bots:
    print(f"[ERROR] Bot '{BOT_NAME}' not found in bots_config.json")
    print(f"Available bots: {', '.join(all_bots.keys())}")
    sys.exit(1)

bot_cfg = all_bots[BOT_NAME]

if not bot_cfg.get("enabled", False):
    print(f"[ERROR] Bot '{BOT_NAME}' is disabled in bots_config.json")
    sys.exit(1)

BOT_TOKEN = bot_cfg.get("bot_token", "").strip()
CHAT_ID   = str(bot_cfg.get("chat_id", "")).strip()
ROLE      = bot_cfg.get("role", "").strip()

if not BOT_TOKEN or not CHAT_ID:
    print(f"[ERROR] Bot '{BOT_NAME}' is missing bot_token or chat_id in bots_config.json")
    sys.exit(1)

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ===========================================================================
# Telegram API helpers
# ===========================================================================
def tg_request(method: str, payload: dict) -> dict:
    url  = f"{API_BASE}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[TG ERROR] {method}: {e}")
        return {}


def send(text: str, parse_mode: str = "HTML") -> None:
    """Send a message to the authorized chat."""
    tg_request("sendMessage", {
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": parse_mode,
    })


def get_updates(offset: int) -> list:
    result = tg_request("getUpdates", {
        "offset":          offset,
        "timeout":         30,
        "allowed_updates": ["message"],
    })
    return result.get("result", [])


# ===========================================================================
# Role: timemachine  —  CHECK / START / STOP / HELP
# ===========================================================================
INDICES = [
    "Nifty 50",
    "Nifty Bank",
    "S&P BSE Sensex",
    "Nifty MidCap Select",
    # "Fin Nifty",  # Commented out as it is currently disabled in tv_scraper.py and start_all.sh
]


def _count_procs(pattern: str) -> int:
    try:
        out = subprocess.check_output(
            ["pgrep", "-fc", pattern], text=True
        ).strip()
        return int(out)
    except Exception:
        return 0


def _read_env_flag(key: str, default=False):
    env_file = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_file):
        return default
    try:
        with open(env_file) as f:
            cfg = json.load(f)
        return cfg.get(key, default)
    except Exception:
        return default


def _get_open_positions() -> list:
    open_pos = []
    today    = datetime.now().strftime("%Y-%m-%d")
    for idx in INDICES:
        sanitized  = idx.replace(" ", "_").replace("&", "_")
        sanitized  = sanitized.replace("__", "_")
        state_file = os.path.join(TRADES_DIR, f"state_{sanitized}.json")
        if not os.path.exists(state_file):
            continue
        try:
            with open(state_file) as f:
                state = json.load(f)
            if state.get("Position", "NONE") != "NONE" and state.get("Date") == today:
                symbol   = state.get("OptionSymbol", "—")
                entry_px = state.get("EntryPrice", 0.0)
                qty      = state.get("Quantity", 0)
                open_pos.append(
                    f"  📌 <b>{idx}</b>: <code>{symbol}</code>\n"
                    f"       Entry ₹{entry_px:.2f} × {qty} shares"
                )
        except Exception:
            pass
    return open_pos


def _run_script(script_name: str, label: str):
    """Run a bash script and send its output as a Telegram reply."""
    script = os.path.join(BASE_DIR, script_name)
    if not os.path.exists(script):
        send(f"❌ <b>{script_name} not found on server!</b>")
        return

    send(f"⏳ Running <code>{script_name}</code>…")
    try:
        result = subprocess.run(
            ["bash", script],
            capture_output=True,
            text=True,
            timeout=90,
            cwd=BASE_DIR,
        )
        status = (
            f"✅ <b>{label} completed</b>"
            if result.returncode == 0
            else f"⚠️ <b>{label} exited with code {result.returncode}</b>"
        )
        parts  = [status]
        stdout = (result.stdout or "").strip()[-2000:]
        stderr = (result.stderr or "").strip()[-500:]
        if stdout:
            parts.append(f"\n<pre>{stdout}</pre>")
        if stderr:
            parts.append(f"\n<b>stderr:</b>\n<pre>{stderr}</pre>")
        send("\n".join(parts))
    except subprocess.TimeoutExpired:
        send(f"⚠️ <b>{script_name} timed out.</b> Check the server manually.")
    except Exception as e:
        send(f"❌ <b>Error:</b> <code>{e}</code>")


# --- Command handlers for 'timemachine' role --------------------------------

def cmd_check():
    now       = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    executors = _count_procs("trade_executor.py")
    chrome    = _count_procs("chrome")
    scraper   = _count_procs("tv_scraper.py")
    expected  = len(INDICES)

    if executors >= expected and chrome >= 1 and scraper >= 1:
        overall = "🟢 All Systems Operational"
    elif executors > 0:
        overall = "🟡 Partially Running"
    else:
        overall = "🔴 System is DOWN"

    paper    = _read_env_flag("paper_trading", True)
    mode_str = "📄 Paper Trading" if paper else "⚡ Live Trading"

    open_pos = _get_open_positions()
    pos_text = "\n".join(open_pos) if open_pos else "  No open positions today"

    send(
        f"<b>🏥 Health Check  —  {BOT_NAME}</b>\n"
        f"<code>{now}</code>\n\n"
        f"<b>Status:</b> {overall}\n"
        f"<b>Mode:</b>   {mode_str}\n\n"
        f"<b>Processes:</b>\n"
        f"  • Executors : {executors}/{expected}\n"
        f"  • Chrome    : {'✅' if chrome  >= 1 else '❌'} ({chrome} proc)\n"
        f"  • TV Scraper: {'✅' if scraper >= 1 else '❌'} ({scraper} proc)\n\n"
        f"<b>Open Positions:</b>\n{pos_text}"
    )


def cmd_start():
    send("🚀 <b>Starting trading system…</b>\n"
         "Killing any stale processes first, then launching fresh instances.")
    _run_script("start_all.sh", "Start")
    time.sleep(8)
    # Auto health-check after start
    cmd_check()


def cmd_stop():
    send("🛑 <b>Stopping trading system…</b>\nExecuting clean shutdown.")
    _run_script("stop_all.sh", "Stop")


def cmd_help():
    send(
        f"<b>🤖 {BOT_NAME}  —  Available Commands</b>\n\n"
        "  <code>CHECK</code> — Health check &amp; open positions\n"
        "  <code>START</code> — Launch the full trading system (fresh)\n"
        "  <code>STOP</code>  — Clean shutdown of all processes\n"
        "  <code>HELP</code>  — This message\n\n"
        "<i>More commands coming soon: PNL, ALLOCATION, STATUS…</i>"
    )


TIMEMACHINE_COMMANDS = {
    "CHECK": cmd_check,
    "START": cmd_start,
    "STOP":  cmd_stop,
    "HELP":  cmd_help,
}


# ===========================================================================
# Role: dhan_algo  —  placeholder (future)
# ===========================================================================
def _dhan_algo_not_ready():
    send("🚧 <b>uk_dhanalgobot</b> commands are not yet implemented.")


DHAN_ALGO_COMMANDS: dict = {}   # populated when the bot is built


# ===========================================================================
# Role registry
# ===========================================================================
ROLE_COMMANDS = {
    "timemachine": TIMEMACHINE_COMMANDS,
    "dhan_algo":   DHAN_ALGO_COMMANDS,
}


# ===========================================================================
# Main polling loop
# ===========================================================================
def main():
    commands = ROLE_COMMANDS.get(ROLE)
    if commands is None:
        print(f"[ERROR] Unknown role '{ROLE}' for bot '{BOT_NAME}'")
        sys.exit(1)

    print(f"[BOT] '{BOT_NAME}' started  |  role={ROLE}  |  chat_id={CHAT_ID}")
    send(
        f"🤖 <b>{BOT_NAME} is online.</b>\n"
        f"Role: <code>{ROLE}</code>\n"
        f"Send <code>HELP</code> to see available commands."
    )

    offset = 0
    while True:
        try:
            updates = get_updates(offset)
        except Exception as e:
            print(f"[BOT] getUpdates error: {e}. Retrying in 5 s…")
            time.sleep(5)
            continue

        for update in updates:
            offset  = update["update_id"] + 1
            msg     = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text    = (msg.get("text") or "").strip().upper()
            if text.startswith("/"):
                text = text[1:]

            # Security gate — silently drop unauthorized senders
            if chat_id != CHAT_ID:
                print(f"[BOT] Ignored unauthorized chat_id: {chat_id}")
                continue

            if not text:
                continue

            print(f"[BOT] [{BOT_NAME}] Command: {text!r}")

            handler = commands.get(text)
            if handler:
                try:
                    handler()
                except Exception as e:
                    send(f"❌ <b>Internal error:</b> <code>{e}</code>")
                    print(f"[BOT] Handler error for {text!r}: {e}")
            else:
                send(
                    f"❓ Unknown command: <code>{text}</code>\n"
                    "Send <code>HELP</code> for available commands."
                )

        # Brief idle pause — long-poll timeout handles most of the waiting
        if not updates:
            time.sleep(1)


if __name__ == "__main__":
    main()

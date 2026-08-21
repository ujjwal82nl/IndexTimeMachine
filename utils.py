import sys
import os
import json
import re
import urllib.request
import time
import struct
import hmac
import hashlib
import base64
from datetime import datetime
import pytz

# ==============================================================================
# GLOBAL CONSTANTS & CONFIGS
# ==============================================================================
# Resolve paths relative to utils.py location so it is independent of CWD
UTILS_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(UTILS_DIR, ".env")
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
SCRIP_MASTER_FILE = os.path.join(UTILS_DIR, "api-scrip-master.csv")
LOCK_DIR = os.path.join(UTILS_DIR, "lock")
DATA_DIR = os.path.join(UTILS_DIR, "data")
LOGS_DIR = os.path.join(UTILS_DIR, "logs")
TRADES_DIR = os.path.join(UTILS_DIR, "trades")
os.makedirs(TRADES_DIR, exist_ok=True)
TRADES_FILE = os.path.join(TRADES_DIR, "trades.json")

IST = pytz.timezone('Asia/Kolkata')

# Unified Index Configuration Metadata List
IndicesList = [
    {
        "Index": "Nifty 50",
        "lotSize": 65,
        "strikeStep": 50,
        "symbol": "NIFTY",
        "expiryStyle": "WEEKLY"
    },
    {
        "Index": "Nifty Bank",
        "lotSize": 30,
        "strikeStep": 100,
        "symbol": "BANKNIFTY",
        "expiryStyle": "MONTHLY"
    },
    {
        "Index": "S&P BSE Sensex",
        "lotSize": 20,
        "strikeStep": 100,
        "symbol": "SENSEX",
        "expiryStyle": "WEEKLY"
    },
    {
        "Index": "Fin Nifty",
        "lotSize": 60,
        "strikeStep": 50,
        "symbol": "FINNIFTY",
        "expiryStyle": "MONTHLY"
    },
    {
        "Index": "Nifty MidCap Select",
        "lotSize": 120,
        "strikeStep": 25,
        "symbol": "MIDCPNIFTY",
        "expiryStyle": "WEEKLY"
    }
]

# ==============================================================================
# UTILITY HELPER FUNCTIONS
# ==============================================================================
def safe_print(text):
    """Outputs safe terminal-friendly stdout strings."""
    print(text.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8'), flush=True)

def get_index_metadata(index_name):
    """Retrieves index configuration attributes from IndicesList."""
    for idx in IndicesList:
        if idx["Index"].upper() == index_name.upper():
            return idx
    # Fallback default values
    return {
        "Index": index_name,
        "lotSize": 30,
        "strikeStep": 100,
        "symbol": re.sub(r'[^A-Za-z0-9]', '', index_name).upper(),
        "expiryStyle": "WEEKLY"
    }

def get_totp_token(secret):
    """Generates a standard 6-digit TOTP token using base64 decoding and hmac (RFC 6238)."""
    secret = secret.replace(" ", "").upper()
    missing_padding = len(secret) % 8
    if missing_padding:
        secret += "=" * (8 - missing_padding)
    key = base64.b32decode(secret, casefold=True)
    
    # 30-second time steps
    intervals_no = int(time.time()) // 30
    msg = struct.pack(">Q", intervals_no)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[19] & 15
    h = (struct.unpack(">I", h[o:o+4])[0] & 0x7fffffff) % 1000000
    return f"{h:06d}"

def download_scrip_master_if_needed():
    """Downloads the compact Dhan instrument master CSV once daily."""
    need_download = True
    if os.path.exists(SCRIP_MASTER_FILE):
        file_time = os.path.getmtime(SCRIP_MASTER_FILE)
        if datetime.fromtimestamp(file_time).date() == datetime.now().date():
            need_download = False
            
    if need_download:
        safe_print("Downloading latest Dhan scrip master CSV...")
        try:
            urllib.request.urlretrieve(SCRIP_MASTER_URL, SCRIP_MASTER_FILE)
            safe_print("Download complete.")
        except Exception as e:
            safe_print(f"Error downloading scrip master: {e}")

# ==============================================================================
# STATE FILE MANAGEMENT
# ==============================================================================
def get_state_filename(underlying):
    sanitized = re.sub(r'[^A-Za-z0-9]', '_', underlying)
    sanitized = re.sub(r'_+', '_', sanitized).strip('_')
    return os.path.join(TRADES_DIR, f"state_{sanitized}.json")

def save_trade_state(filename, state):
    try:
        with open(filename, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        safe_print(f"Error saving trade state: {e}")

def load_trade_state(filename, underlying):
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                state = json.load(f)
            today_str = datetime.now(IST).strftime("%Y-%m-%d")
            if state.get("Date") == today_str and state.get("Underlying") == underlying:
                return state
        except Exception as e:
            safe_print(f"Error loading state file: {e}")
    return None

def is_paper_trading():
    """Reads .env configuration to check if paper trading mode is enabled."""
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r") as f:
                config = json.load(f)
                return config.get("paper_trading", False)
        except Exception:
            pass
    return False

def is_indicator_strategy_enabled():
    """Reads .env configuration to check if the Indicator Strategy is enabled."""
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r") as f:
                config = json.load(f)
                return config.get("indicator_strategy_enabled", False)
        except Exception:
            pass
    return False

def get_indicator_state_filename(underlying):
    """Returns the path to the Indicator Strategy state file for the given underlying."""
    sanitized = re.sub(r'[^A-Za-z0-9]', '_', underlying)
    sanitized = re.sub(r'_+', '_', sanitized).strip('_')
    return os.path.join(TRADES_DIR, f"state_{sanitized}_indicator.json")

lock_fp = None

def acquire_lock(index_name):
    global lock_fp
    os.makedirs(LOCK_DIR, exist_ok=True)
    lock_file = os.path.join(LOCK_DIR, f".lock_{index_name.replace(' ', '_')}.lock")
    try:
        lock_fp = open(lock_file, "w")
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock_fp.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Write PID for diagnostic purposes
        lock_fp.write(str(os.getpid()))
        lock_fp.flush()
        return True
    except (ImportError, IOError, OSError):
        if lock_fp:
            try:
                lock_fp.close()
            except Exception:
                pass
            lock_fp = None
        return False

def send_telegram_notification(message):
    """Sends a notification message to the configured Telegram chat (HTML mode)."""
    if not os.path.exists(ENV_FILE):
        return
    try:
        with open(ENV_FILE, "r") as f:
            config = json.load(f)
            bot_cfg = config.get("telegram_bot", {})
            chat_id = bot_cfg.get("chat_id")
            bot_token = bot_cfg.get("bot_token")
            
        if chat_id and bot_token:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                pass
    except Exception as e:
        safe_print(f"[TELEGRAM ERROR] Failed to send notification: {e}")


# ==============================================================================
# REAL-TIME TRADE LEDGER LOGGING
# ==============================================================================
_INDEX_MAP = {
    "nifty50": "Nifty 50", "nifty 50": "Nifty 50",
    "niftybank": "Nifty Bank", "nifty bank": "Nifty Bank", "banknifty": "Nifty Bank",
    "niftymidcapselect": "Nifty Midcap Select", "nifty midcap select": "Nifty Midcap Select",
    "nifty midcap": "Nifty Midcap Select", "midcpnifty": "Nifty Midcap Select",
    "sandpbsesensex": "S&P BSE Sensex", "s&p bse sensex": "S&P BSE Sensex", "sensex": "S&P BSE Sensex",
}

def _to_index_name(u: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", u.lower())
    for k, v in _INDEX_MAP.items():
        if re.sub(r"[^a-z0-9]", "", k) == key:
            return v
    return u.title()

def _append_trade_to_ledger_file(filepath, trade_record):
    ledger_data = {
        "_meta": {
            "schema_version": 1,
            "last_extracted": None
        },
        "trades": []
    }
    
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                ledger_data = json.load(f)
        except Exception:
            pass
            
    # Deduplication check based on Index, Strategy, EntryTime, OptionSymbol, Quantity
    dedup_keys = set()
    for t in ledger_data.get("trades", []):
        key = (t.get("Index", ""), t.get("Strategy", ""), t.get("EntryTime", ""), t.get("OptionSymbol", ""), str(t.get("Quantity", "")))
        dedup_keys.add(key)
        
    new_key = (trade_record.get("Index", ""), trade_record.get("Strategy", ""), trade_record.get("EntryTime", ""), trade_record.get("OptionSymbol", ""), str(trade_record.get("Quantity", "")))
    
    if new_key not in dedup_keys:
        ledger_data.setdefault("trades", []).append(trade_record)
        ledger_data["_meta"]["last_extracted"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        
        # Save ledger atomically
        tmp_path = filepath + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(ledger_data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, filepath)
        return True
    return False

def log_open_trade_to_ledger(trade_record):
    """
    Logs an open trade record (BUY order) to today's daily ledger file trades/trade_ledger_YYYY-MM-DD.json.
    """
    try:
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        daily_ledger_file = os.path.join(TRADES_DIR, f"trade_ledger_{today_str}.json")
        success = _append_trade_to_ledger_file(daily_ledger_file, trade_record)
        if success:
            safe_print(f"[LEDGER] Open trade logged to daily ledger: {os.path.basename(daily_ledger_file)}")
        return success
    except Exception as e:
        safe_print(f"[ERROR] Failed to log open trade: {e}")
        return False

def log_completed_trade_to_ledger(symbol, qty, exit_details, fallback_record=None):
    """
    Finds the open trade for OptionSymbol and Quantity with ExitTime=None in today's daily ledger,
    and updates it with exit_details. If no open trade matches, appends fallback_record if provided.
    """
    try:
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        filepath = os.path.join(TRADES_DIR, f"trade_ledger_{today_str}.json")
        
        # Load or initialize the daily ledger
        ledger_data = {
            "_meta": {
                "schema_version": 1,
                "last_extracted": None
            },
            "trades": []
        }
        
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    ledger_data = json.load(f)
            except Exception:
                pass
                
        trades = ledger_data.get("trades", [])
        updated = False
        
        # Match from the end (newest first) to find the most recent open trade
        for t in reversed(trades):
            if t.get("OptionSymbol") == symbol and int(t.get("Quantity", 0)) == int(qty) and t.get("ExitTime") is None:
                t.update(exit_details)
                updated = True
                break
                
        if updated:
            ledger_data["_meta"]["last_extracted"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
            tmp = filepath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(ledger_data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, filepath)
            safe_print(f"[LEDGER] Completed trade updated in daily ledger: {os.path.basename(filepath)}")
            return True
        elif fallback_record:
            # Fallback: append completed record
            fallback_record.update(exit_details)
            ledger_data.setdefault("trades", []).append(fallback_record)
            ledger_data["_meta"]["last_extracted"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
            tmp = filepath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(ledger_data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, filepath)
            safe_print(f"[LEDGER] No open trade found. Appended new completed trade to daily ledger: {os.path.basename(filepath)}")
            return True
        else:
            safe_print(f"[LEDGER] [WARN] No matching open trade found in ledger for {symbol} qty {qty}.")
            return False
    except Exception as e:
        safe_print(f"[ERROR] Failed to update completed trade: {e}")
        return False


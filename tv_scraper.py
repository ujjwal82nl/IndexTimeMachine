import sys
import time
import os
import urllib.request
import json
import websocket
import re
from datetime import datetime

# Port for Chrome remote debugging
PORT = 9223
CDP_URL = "http://127.0.0.1:" + str(PORT)

# List of indices to rotate through and scrape
# TICKERS = [
#     "NSE:NIFTY",         # Nifty 50
#     "NSE:BANKNIFTY",     # Bank Nifty
#     "BSE:SENSEX",        # Sensex
#     "NSE:CNXFINANCE",    # Fin Nifty
#     "NSE:NIFTY_MID_SELECT" # Nifty MidCap Select
# ]
TICKERS = [
    "NSE:NIFTY",         # Nifty 50
    "NSE:BANKNIFTY",     # Bank Nifty
    "BSE:SENSEX",        # Sensex
    "NSE:NIFTY_MID_SELECT" # Nifty MidCap Select
]

# Map tickers to clean display names
# TICKER_NAMES = {
#     "NSE:NIFTY": "Nifty 50",
#     "NSE:BANKNIFTY": "Nifty Bank",
#     "BSE:SENSEX": "S&P BSE Sensex",
#     "NSE:CNXFINANCE": "Fin Nifty",
#     "NSE:NIFTY_MID_SELECT": "Nifty MidCap Select"
# }
TICKER_NAMES = {
    "NSE:NIFTY": "Nifty 50",
    "NSE:BANKNIFTY": "Nifty Bank",
    "BSE:SENSEX": "S&P BSE Sensex",
    "NSE:NIFTY_MID_SELECT": "Nifty MidCap Select"
}

def safe_print(text):
    """Safely prints text supporting Windows terminal encodings (replaces unencodable characters)."""
    print(text.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8'))

def get_tradingview_ws_url():
    """Queries Chrome's HTTP endpoint to find the active TradingView tab's WebSocket debugger URL."""
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json", timeout=2) as response:
            targets = json.loads(response.read().decode())
            for t in targets:
                if t.get("type") == "page" and "tradingview.com" in t.get("url", ""):
                    return t.get("webSocketDebuggerUrl")
    except Exception:
        pass
    return None

def reconstruct_month_with_future_check(day, month_str, time_str):
    """
    Checks if a parsed date is in the future. If so, shifts it to the previous month.
    (e.g., August 31st when today is August 2nd will correctly shift to July 31st).
    Does not shift the month if the parsed target is later today.
    """
    now = datetime.now()
    year = now.year
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    try:
        dt_obj = datetime.strptime(f"{year} {day} {month_str} {time_str}", "%Y %d %b %H:%M")
        # Only shift to previous month if target is on a different calendar day in the future
        if dt_obj > now and dt_obj.date() != now.date():
            idx = months.index(month_str)
            prev_idx = (idx - 1) % 12
            prev_month = months[prev_idx]
            target_year = year - 1 if month_str == "Jan" else year
            
            # Test parse
            datetime.strptime(f"{target_year} {day} {prev_month} {time_str}", "%Y %d %b %H:%M")
            return prev_month
    except Exception:
        pass
    return month_str

def parse_direct_table_cells(cells):
    """
    Parses structured cells list to extract the Strategy TIME MACHINE table parameters.
    """
    # Group cells by row
    rows = {}
    for cell in cells:
        row_idx = cell.get("row")
        col_idx = cell.get("col")
        text = cell.get("text", "").strip()
        if row_idx not in rows:
            rows[row_idx] = {}
        rows[row_idx][col_idx] = text
        
    operator = "--"
    bar2_time = "--"
    bar3_time = "--"
    time_candle = "--"
    high_val = "--"
    low_val = "--"
    entry_val = "--"
    sl_val = "--"
    target_val = "--"
    
    # 1. Parse Operator by scanning all cell texts for keywords
    for r_idx, cols in rows.items():
        for c_idx, text in cols.items():
            txt_upper = text.upper()
            if "RISE" in txt_upper or "SELL" in txt_upper:
                operator = "Sell on Rise"
                break
            elif "DIP" in txt_upper or "BUY" in txt_upper:
                operator = "Buy on Dip"
                break
        if operator != "--":
            break
            
    # 2. Parse Bar times by scanning cells for date patterns
    date_patterns = []
    for r_idx, cols in rows.items():
        for c_idx, text in cols.items():
            matches = re.findall(r'\b\d{1,2}\s+(?:[A-Za-z]{1,3}\s+)?\d{2}:\d{2}\b', text)
            for m in matches:
                date_patterns.append(m)
                
    current_month = datetime.now().strftime("%b")
    best_matches = {}
    for dt in date_patterns:
        parts = dt.split()
        day = parts[0]
        time_str = parts[-1]
        
        has_explicit_month = False
        month = current_month
        if len(parts) == 3:
            candidate_month = parts[1]
            if len(candidate_month) == 3 and candidate_month.isalpha():
                month = candidate_month.capitalize()
                has_explicit_month = True
                
        month = reconstruct_month_with_future_check(day, month, time_str)
        full_str = f"{day} {month} {time_str}"
        key = (day, time_str)
        if key not in best_matches or (has_explicit_month and not best_matches[key][0]):
            best_matches[key] = (has_explicit_month, full_str)
            
    unique_dates = [val[1] for val in best_matches.values()]
    parsed_dates = []
    for ud in unique_dates:
        try:
            dt_obj = datetime.strptime(f"{datetime.now().year} {ud}", "%Y %d %b %H:%M")
            parsed_dates.append((dt_obj, ud))
        except Exception:
            pass
    parsed_dates.sort(key=lambda x: x[0])
    formatted_dates = [x[1] for x in parsed_dates]
    
    if len(formatted_dates) >= 2:
        bar2_time = formatted_dates[0]
        bar3_time = formatted_dates[1]
    elif len(formatted_dates) == 1:
        bar2_time = formatted_dates[0]
        
    # 3. Parse Time Candle
    for r_idx, cols in rows.items():
        col0 = cols.get(0, "").lower()
        if "time candel" in col0 or "time candle" in col0:
            tc_val = cols.get(1, "")
            if tc_val:
                time_candle = tc_val.strip()
                break
                
    # 4. Parse Status / Target Word
    for r_idx, cols in rows.items():
        for c_idx, text in cols.items():
            txt_upper = text.upper()
            if "CLOSED" in txt_upper:
                target_val = "CLOSED"
            elif "OPEN" in txt_upper:
                target_val = "OPEN"
                
    # 5. Parse Prices (High, Low, Entry, SL)
    numeric_prices = []
    for r_idx, cols in rows.items():
        for c_idx, text in cols.items():
            prices = re.findall(r'\b\d{1,3}[,.]\d{3}(?:[.,]\d{2})?\b|\b\d{5,6}\b', text)
            for p in prices:
                clean_p = re.sub(r'[^0-9]', '', p)
                if clean_p:
                    val = int(clean_p)
                    if val not in numeric_prices:
                        numeric_prices.append(val)
                        
    numeric_prices.sort(reverse=True)
    
    # Try direct label lookup first (highly precise)
    high_lbl_val = None
    low_lbl_val = None
    entry_lbl_val = None
    sl_lbl_val = None
    
    for r_idx, cols in rows.items():
        col0 = cols.get(0, "").upper()
        col1 = cols.get(1, "")
        if "HIGH" in col0:
            high_lbl_val = col1
        elif "LOW" in col0:
            low_lbl_val = col1
        elif "ENTRY" in col0:
            entry_lbl_val = col1
        elif "SL" in col0 or "STOP" in col0:
            sl_lbl_val = col1
            
    if high_lbl_val and high_lbl_val != "--":
        high_val = high_lbl_val
    if low_lbl_val and low_lbl_val != "--":
        low_val = low_lbl_val
    if entry_lbl_val and entry_lbl_val != "--":
        entry_val = entry_lbl_val
    if sl_lbl_val and sl_lbl_val != "--":
        sl_val = sl_lbl_val
        
    # Fallback to sorted price heuristic if label lookup was empty
    if high_val == "--" and low_val == "--" and len(numeric_prices) == 4:
        if operator == "Sell on Rise":
            sl_val = f"{numeric_prices[0]:,}"
            high_val = f"{numeric_prices[1]:,}"
            low_val = f"{numeric_prices[2]:,}"
            entry_val = f"{numeric_prices[3]:,}"
        else:
            entry_val = f"{numeric_prices[0]:,}"
            high_val = f"{numeric_prices[1]:,}"
            low_val = f"{numeric_prices[2]:,}"
            sl_val = f"{numeric_prices[3]:,}"
    elif high_val == "--" and low_val == "--" and len(numeric_prices) == 3:
        if operator == "Sell on Rise":
            sl_val = f"{numeric_prices[0]:,}"
            high_val = f"{numeric_prices[1]:,}"
            low_val = f"{numeric_prices[2]:,}"
        else:
            entry_val = f"{numeric_prices[0]:,}"
            high_val = f"{numeric_prices[1]:,}"
            low_val = f"{numeric_prices[2]:,}"
    elif high_val == "--" and low_val == "--" and len(numeric_prices) == 2:
        high_val = f"{numeric_prices[0]:,}"
        low_val = f"{numeric_prices[1]:,}"
    elif entry_val == "--" and len(numeric_prices) == 1:
        entry_val = f"{numeric_prices[0]:,}"
        
    return operator, bar2_time, bar3_time, time_candle, high_val, low_val, entry_val, sl_val, target_val

def get_report_filename(underlying):
    """
    Sanitizes the underlying name to construct a filename (e.g. "Nifty Bank" -> "data/scrap_Nifty_Bank.json")
    """
    sanitized = re.sub(r'[^A-Za-z0-9]', '_', underlying)
    sanitized = re.sub(r'_+', '_', sanitized)
    sanitized = sanitized.strip('_')
    scraper_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(scraper_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, f"scrap_{sanitized}.json")

def log_to_report(current_data):
    """
    Appends current_data to a unique filename derived from the underlying asset title,
    if the state is different from the last logged state in that specific file.
    """
    underlying = current_data.get("Underlying", "Unknown")
    filename = get_report_filename(underlying)
    data_list = []
    
    # Read existing entries
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                content = f.read().strip()
                if content:
                    data_list = json.loads(content)
        except Exception as e:
            safe_print(f"Error reading {filename}: {e}")
            
    # Check if this state is identical to the last logged state
    should_log = True
    if data_list:
        last_entry = data_list[-1].copy()
        last_entry.pop("Timestamp", None)
        
        current_compare = current_data.copy()
        current_compare.pop("Timestamp", None)
        
        if last_entry == current_compare:
            should_log = False
            
    if should_log:
        current_data["Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today_date = datetime.now().strftime("%Y-%m-%d")
        
        # Check if the last entry is from today
        is_today_entry = False
        if data_list:
            last_timestamp = data_list[-1].get("Timestamp", "")
            if last_timestamp.startswith(today_date):
                is_today_entry = True
                
        if is_today_entry:
            # Overwrite today's entry with the latest run data
            data_list[-1] = current_data
        else:
            # Append new entry for a new day
            data_list.append(current_data)
            
        try:
            with open(filename, "w") as f:
                json.dump(data_list, f, indent=2)
            safe_print(f"  [SAVED] New trade state logged to {filename}")
        except Exception as e:
            safe_print(f"Error writing to {filename}: {e}")
    else:
        safe_print(f"  [REPORT] No state changes detected in {filename}. Logging skipped.")

def log_indicator_signals(underlying, trades):
    """
    Saves and merges completed trades from the strategy tester
    into data/indicator_signals_<index>.json, grouped by date.
    """
    if not trades:
        return
        
    sanitized = re.sub(r'[^A-Za-z0-9]', '_', underlying)
    sanitized = re.sub(r'_+', '_', sanitized).strip('_')
    scraper_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(scraper_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    filename = os.path.join(data_dir, f"indicator_signals_{sanitized}.json")
    
    # 1. Group trades by their entry date
    new_signals = {}
    for t in trades:
        try:
            entry_tm = t.get("entry_time")
            exit_tm = t.get("exit_time")
            if not entry_tm:
                continue
                
            # Convert timestamps (ms) to datetime
            entry_dt = datetime.fromtimestamp(entry_tm / 1000)
            date_str = entry_dt.strftime("%Y-%m-%d")
            entry_time_str = entry_dt.strftime("%H:%M")
            
            exit_time_str = ""
            if exit_tm:
                exit_dt = datetime.fromtimestamp(exit_tm / 1000)
                exit_time_str = exit_dt.strftime("%H:%M")
                
            trade_record = {
                "trade_id": t.get("trade_index"),
                "entry_time": entry_time_str,
                "entry_type": "LONG (CALL)" if t.get("entry_type") == "le" else "SHORT (PUT)",
                "entry_signal": t.get("entry_comment"),
                "entry_price": t.get("entry_price"),
                "exit_time": exit_time_str,
                "exit_signal": t.get("exit_comment"),
                "exit_price": t.get("exit_price"),
                "profit_points": t.get("profit")
            }
            
            if date_str not in new_signals:
                new_signals[date_str] = []
                
            # Avoid duplicate listings in the active batch
            if not any(x["trade_id"] == trade_record["trade_id"] for x in new_signals[date_str]):
                new_signals[date_str].append(trade_record)
        except Exception:
            continue
            
    if not new_signals:
        return
        
    # 2. Load existing records
    existing = {}
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                existing = json.load(f)
        except Exception:
            pass
            
    # 3. Merge: merge by trade_id
    for d_str, trade_list in new_signals.items():
        if d_str not in existing:
            existing[d_str] = []
            
        for new_t in trade_list:
            found = False
            for idx, ext_t in enumerate(existing[d_str]):
                if ext_t.get("trade_id") == new_t["trade_id"]:
                    # Update with latest values (e.g. if the trade just closed, exit_time/exit_signal will be updated)
                    existing[d_str][idx] = new_t
                    found = True
                    break
            if not found:
                existing[d_str].append(new_t)
                
        # Sort trades for this date to keep them chronologically ordered
        existing[d_str].sort(key=lambda x: x.get("trade_id", 0))
        
    # 4. Save merged history back to file
    try:
        with open(filename, "w") as f:
            json.dump(existing, f, indent=2)
        safe_print(f"  [SAVED] Merged indicator trades list logged to {filename}")
    except Exception as e:
        safe_print(f"Error writing trades to {filename}: {e}")

def change_chart_symbol_via_url(ws, ticker):
    """Triggers native browser URL redirection to shift active chart symbol, then closes WebSocket connection."""
    url = f"https://www.tradingview.com/chart/?symbol={ticker}"
    safe_print(f"Shifting chart to: {url}...")
    try:
        # Evaluate redirect in the page context
        ws.send(json.dumps({
            "id": 401,
            "method": "Runtime.evaluate",
            "params": {
                "expression": f"window.location.href = '{url}';"
            }
        }))
    except Exception as e:
        safe_print(f"Error requesting redirect: {e}")
        
    try:
        ws.close()
    except Exception:
        pass

def main():
    safe_print(f"Connecting to Chrome DevTools on {CDP_URL}...")
    
    try:
        urllib.request.urlopen(CDP_URL, timeout=2)
    except Exception:
        safe_print(f"\n[ERROR] Chrome debugging port {PORT} is not reachable.")
        sys.exit(1)

    # Initial connection
    ws_url = None
    opened_once = False
    while not ws_url:
        ws_url = get_tradingview_ws_url()
        if not ws_url:
            if not opened_once:
                safe_print("TradingView tab not found in Chrome. Opening one programmatically...")
                try:
                    req = urllib.request.Request(f"{CDP_URL}/json/new", method="PUT")
                    with urllib.request.urlopen(req, timeout=5) as response:
                        res_data = json.loads(response.read().decode())
                        new_ws_url = res_data.get("webSocketDebuggerUrl")
                        
                    if new_ws_url:
                        opened_once = True
                        safe_print("Blank tab opened. Connecting debugger to navigate it to TradingView...")
                        ws_temp = websocket.create_connection(new_ws_url, timeout=5)
                        navigate_cmd = {
                            "id": 1001,
                            "method": "Page.navigate",
                            "params": {
                                "url": "https://www.tradingview.com"
                            }
                        }
                        ws_temp.send(json.dumps(navigate_cmd))
                        time.sleep(1) # Give the navigate request a moment to transmit
                        ws_temp.close()
                        safe_print("Navigation command sent. Waiting for page to resolve and load...")
                        time.sleep(8)
                except Exception as e:
                    safe_print(f"Error opening new tab: {e}")
                    time.sleep(3)
            else:
                safe_print("Waiting for page load to resolve to tradingview.com...")
                time.sleep(4)
            
    safe_print(f"Found TradingView tab. Attaching WebSocket debugger...")
    
    try:
        ws = websocket.create_connection(ws_url, timeout=10)
    except Exception as e:
        safe_print(f"\n[ERROR] Connection to WebSocket failed: {e}")
        sys.exit(1)
        
    safe_print("Debugger connected successfully! Starting rotating scraper daemon. Press Ctrl+C to stop.\n")
    
    # Unified DOM/graphics scraping script
    js_code = """
    (async () => {
      let changed = false;
      try {
        var chart = window.TradingViewApi._activeChartWidgetWV.value()._chartWidget;
        var model = chart.model();
        var sources = model.model().dataSources();
        var target_source = null;
        for (var si = 0; si < sources.length; si++) {
          var s = sources[si];
          if (!s.metaInfo) continue;
          var meta = s.metaInfo();
          var name = meta.description || meta.shortDescription || '';
          if (name.indexOf("Time Machine") !== -1) {
            target_source = s;
            break;
          }
        }
        if (target_source) {
          var props = target_source.properties().inputs;
          // Check and set Operator Mode (in_8)
          if (props.in_8 && props.in_8.value() !== true) {
            model.setProperty(props.in_8, true);
            changed = true;
          }
          // Check and set Enable Trailing SL (in_2)
          if (props.in_2 && props.in_2.value() !== true) {
            model.setProperty(props.in_2, true);
            changed = true;
          }
          // Check and set Show T1 - T5 (in_3 to in_7)
          for (var t = 3; t <= 7; t++) {
            var propName = 'in_' + t;
            if (props[propName] && props[propName].value() !== true) {
              model.setProperty(props[propName], true);
              changed = true;
            }
          }
        }
      } catch (e) {
        // ignore errors during property set
      }

      // If we modified any property, wait 1000ms for study to recalculate
      if (changed) {
        await new Promise(resolve => setTimeout(resolve, 1000));
      }

      const legends = [];
      const elements = document.querySelectorAll('[class*="item-"][class*="series-"], [class*="item-"][class*="study-"]');
      elements.forEach(el => {
          const text = (el.innerText || el.textContent || "").trim().replace(/\\s+/g, ' ');
          if (text) legends.push(text);
      });

      let tableCells = null;
      let errorMsg = null;
      try {
        var chart = window.TradingViewApi._activeChartWidgetWV.value()._chartWidget;
        var model = chart.model();
        var sources = model.model().dataSources();
        var target_source = null;
        for (var si = 0; si < sources.length; si++) {
          var s = sources[si];
          if (!s.metaInfo) continue;
          var meta = s.metaInfo();
          var name = meta.description || meta.shortDescription || '';
          if (name.indexOf("Time Machine") !== -1) {
            target_source = s;
            break;
          }
        }
        var signals = [];
        if (target_source) {
          // 1. Extract table cells
          var g = target_source._graphics;
          if (g && g._primitivesCollection) {
            var pc = g._primitivesCollection;
            var tcOuter = pc.dwgtablecells;
            if (tcOuter) {
              var tcInner = tcOuter.get('tableCells');
              if (tcInner && tcInner._primitivesDataById) {
                tableCells = [];
                tcInner._primitivesDataById.forEach(function(v, id) {
                  tableCells.push({
                    row: v.row,
                    col: v.col,
                    text: v.t || ''
                  });
                });
              }
            }
          }
          
          // 2. Extract completed trades from Strategy Tester
          var trades = [];
          if (target_source._reportData && target_source._reportData.trades) {
            var tList = target_source._reportData.trades;
            for (var i = 0; i < tList.length; i++) {
              var t = tList[i];
              if (t) {
                trades.push({
                  trade_index: i,
                  entry_comment: t.e ? t.e.c : '',
                  entry_price: t.e ? t.e.p : 0,
                  entry_time: t.e ? t.e.tm : 0,
                  entry_type: t.e ? t.e.tp : '',
                  exit_comment: t.x ? t.x.c : '',
                  exit_price: t.x ? t.x.p : 0,
                  exit_time: t.x ? t.x.tm : 0,
                  exit_type: t.x ? t.x.tp : '',
                  profit: t.tp ? t.tp.v : 0
                });
              }
            }
          }
        }
      } catch(e) {
        errorMsg = e.toString();
      }
      return {legends: legends, cells: tableCells, error: errorMsg, settingsApplied: changed, trades: trades};
    })()
    """
    
    cmd_scrape = {
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {
            "expression": js_code,
            "returnByValue": True,
            "awaitPromise": True
        }
    }
    
    try:
        for current_ticker in TICKERS:
            underlying = TICKER_NAMES.get(current_ticker, "Unknown")
            
            # Clear terminal screen
            os.system('cls' if os.name == 'nt' else 'clear')
            
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print("==================================================")
            print(f" TradingView Indicator Scraper - {now}")
            print(f" Scrape target: {current_ticker} ({underlying})")
            print("==================================================")
            
            # Switch symbol using URL redirection
            try:
                change_chart_symbol_via_url(ws, current_ticker)
            except Exception as e:
                safe_print(f"Error shifting symbol: {e}")
                
            safe_print("Waiting 10 seconds for page reload and chart layout init...")
            time.sleep(10)
            
            # Reconnect to active debugging socket
            ws_url = None
            for _ in range(5):
                ws_url = get_tradingview_ws_url()
                if ws_url:
                    break
                time.sleep(2)
                
            if not ws_url:
                safe_print("[ERROR] Failed to locate TradingView tab after reload. Skipping...")
                continue
                
            try:
                ws = websocket.create_connection(ws_url, timeout=10)
            except Exception as e:
                safe_print(f"[ERROR] Failed to reconnect to TradingView tab: {e}. Skipping...")
                continue
                
            # Perform direct memory scrape
            try:
                ws.send(json.dumps(cmd_scrape))
                response = json.loads(ws.recv())
                result = response.get("result", {})
                eval_res = result.get("result", {}).get("value", {})
                
                # 1. Output legends
                legends = eval_res.get("legends", [])
                if not legends:
                    safe_print("No indicators found in the legend.")
                else:
                    safe_print("\n[Legend Values (DOM)]")
                    for ind in legends:
                        safe_print(f"  • {ind}")
                        
                # 2. Extract and parse cells
                cells = eval_res.get("cells")
                err = eval_res.get("error")
                applied = eval_res.get("settingsApplied")
                
                if applied:
                    safe_print("\n[SETTINGS] Auto-configured indicator inputs (Operator Mode, Trailing SL, Show Targets).")
                    
                if err:
                    safe_print(f"\n[ERROR] Internal TradingView state extraction failed: {err}")
                    
                if not cells:
                    safe_print("\n[WARNING] Strategy TIME MACHINE table data is not visible or empty.")
                else:
                    try:
                        with open("debug_cells.json", "w") as df:
                            json.dump(cells, df, indent=2)
                    except Exception:
                        pass
                    op, b2, b3, tc, high, low, entry, sl, target = parse_direct_table_cells(cells)
                    
                    safe_print("\n[Strategy TIME MACHINE Table (Direct Scrape)]")
                    safe_print(f"  • Underlying:    {underlying}")
                    safe_print(f"  • Operator:      {op}")
                    safe_print(f"  • Bar 2 Time:    {b2}")
                    safe_print(f"  • Bar 3 Time:    {b3}")
                    safe_print(f"  • Time Candle:   {tc}")
                    safe_print(f"  • High:          {high}")
                    safe_print(f"  • Low:           {low}")
                    safe_print(f"  • ENTRY:         {entry}")
                    safe_print(f"  • SL:            {sl}")
                    safe_print(f"  • TARGET (1:2):  {target}")
                    
                    # 3. Log structured JSON values to report file if changed
                    trade_record = {
                        "Underlying": underlying,
                        "Strategy": "TIME MACHINE",
                        "Operator": op,
                        "Bar 2 Time": b2,
                        "Bar 3 Time": b3,
                        "Time Candle": tc,
                        "High": high,
                        "Low": low,
                        "ENTRY": entry,
                        "SL": sl,
                        "TARGET (1:2)": target,
                        "Timestamp": ""  # populated in logging function
                    }
                    log_to_report(trade_record)
                    log_indicator_signals(underlying, eval_res.get("trades", []))
            except Exception as e:
                safe_print(f"Error executing evaluation script: {e}")
                
            print("\n==================================================")
            print("Sleeping for 5 seconds before next index...")
            time.sleep(5)
            
        safe_print("\nAll indices processed successfully. Exiting.")
            
    except KeyboardInterrupt:
        print("\nStopping scraper...")
    finally:
        try:
            ws.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()

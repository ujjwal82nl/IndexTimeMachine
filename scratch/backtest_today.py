import sys
import json
import os
import pandas as pd
from datetime import datetime, time, timedelta

# Resolve and append project root directory dynamically
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from utils import ENV_FILE, IST, get_totp_token, DATA_DIR
from broker import get_intraday_candles, get_atm_itm_strike, find_option_security_id

def get_dynamic_expiry_date_for_date(underlying_symbol, query_dt, expiry_style="WEEKLY"):
    try:
        from broker import SCRIP_MASTER_FILE, get_index_metadata
        df = pd.read_csv(SCRIP_MASTER_FILE, low_memory=False)
        meta = get_index_metadata(underlying_symbol)
        base_symbol = meta["symbol"]
        
        df_opts = df[
            df['SEM_TRADING_SYMBOL'].str.startswith(base_symbol) &
            (df['SEM_TRADING_SYMBOL'].str.endswith('CE') | df['SEM_TRADING_SYMBOL'].str.endswith('PE'))
        ].copy()
        
        df_opts['expiry_dt'] = pd.to_datetime(df_opts['SEM_EXPIRY_DATE'], errors='coerce')
        df_opts = df_opts.dropna(subset=['expiry_dt'])
        
        future_dates = df_opts[df_opts['expiry_dt'].dt.date >= query_dt]['expiry_dt'].dt.date.unique()
        unique_expiries = sorted(list(future_dates))
        
        if not unique_expiries:
            return None
            
        # Standard skipped logic (skip same week or < 3 days)
        query_calendar_week = query_dt.isocalendar()[1]
        query_year = query_dt.year
        
        for expiry in unique_expiries:
            if expiry.isocalendar()[1] == query_calendar_week and expiry.year == query_year:
                continue
            if (expiry - query_dt).days < 3:
                continue
            return expiry
            
        return unique_expiries[0]
    except Exception as e:
        print(f"Error calculating expiry date: {e}")
        return None

def print_sizing_card(entry_idx, stop_loss_idx, opt_price):
    from utils import get_index_metadata
    meta = get_index_metadata("Nifty 50")
    lot_size = meta["lotSize"]
    
    idx_risk = abs(entry_idx - stop_loss_idx)
    strike_risk = idx_risk * 0.5
    risk_per_lot = strike_risk * lot_size
    
    target_lots = round(5000.0 / risk_per_lot) if risk_per_lot > 0 else 1
    if target_lots < 1:
        target_lots = 1
        
    # Scale down lots if cost exceeds 80% allocation of 1,00,000 capital (80,000 INR limit)
    capital_limit = 100000.0
    budget_limit = capital_limit * 0.8
    
    initial_lots = target_lots
    while target_lots > 1:
        cost = target_lots * lot_size * opt_price
        if cost <= budget_limit:
            break
        target_lots -= 1
        
    qty = target_lots * lot_size
    cost = qty * opt_price
    
    print("\n" + "-"*50)
    print("             MARGIN BUDGET SIZING")
    print("-"*50)
    print(f"  • Entry Index:        {entry_idx:.2f}")
    print(f"  • Stop Loss Index:    {stop_loss_idx:.2f}")
    print(f"  • Index Risk Points:  {idx_risk:.2f}")
    print(f"  • Strike Risk Points: {strike_risk:.2f}")
    print(f"  • Risk per Lot:       {risk_per_lot:.2f}")
    print(f"  • Target Lots (Risk): {initial_lots} lots")
    print(f"  • Allocated Lots:     {target_lots} lots ({qty} shares) [Capped at 80k Budget]")
    print(f"  • Cost for Target:    {cost:.2f} ({qty} shares @ {opt_price:.2f})")
    print("-"*50 + "\n")
    return target_lots, qty

# Load credentials
with open(ENV_FILE, "r") as f:
    config = json.load(f)
    dhan_config = config.get("dhan_config", {})
    client_code = dhan_config.get("client_code")
    totp_secret = dhan_config.get("totp_secret")
    pin = dhan_config.get("pin")

from broker import authenticate_and_get_dhan_client
dhan, access_token = authenticate_and_get_dhan_client(client_code, totp_secret, pin, force_refresh=False)

# Parse target date from arguments (default to yesterday: 2026-08-04)
query_date = sys.argv[1] if len(sys.argv) > 1 else "2026-08-04"

# Scrape file
scrape_file = os.path.join(DATA_DIR, "scrap_Nifty_50.json")
if not os.path.exists(scrape_file):
    print("Scraper output file not found!")
    sys.exit(1)

with open(scrape_file, "r") as f:
    scrape_data = json.load(f)

# Find signal matching query_date
last_signal = None
try:
    query_dt = datetime.strptime(query_date, "%Y-%m-%d").date()
    query_day_month = query_dt.strftime("%d %b") # e.g. "05 Aug"
except Exception:
    query_day_month = None

for entry in scrape_data:
    # 1. Primary check: Timestamp starts with the query_date (e.g., "2026-08-05")
    if entry.get("Timestamp", "").startswith(query_date):
        last_signal = entry
        break
        
    # 2. Fallback check: Compare Day & Month from "Bar 2 Time" (e.g., "05 Aug 11:25")
    # This handles timezone differences or when the scraper was run after midnight (the next calendar day)
    b2_time = entry.get("Bar 2 Time", "")
    if query_day_month and b2_time and b2_time != "--":
        try:
            parts = b2_time.split()
            if len(parts) >= 2:
                day_str = parts[0]
                month_str = parts[1]
                entry_day_month = f"{int(day_str):02d} {month_str.capitalize()}"
                if query_day_month == entry_day_month:
                    last_signal = entry
                    break
        except Exception:
            pass

if not last_signal:
    print(f"Could not find a signal in scraper file for date {query_date}!")
    sys.exit(1)

bar2_time_str = last_signal.get("Bar 2 Time") 
bar3_time_str = last_signal.get("Bar 3 Time") 
operator = last_signal.get("Operator")        

print("==================================================")
print(f" BACKTEST SIMULATION FOR NIFTY 50 ({query_date})")
print("==================================================")
print(f"Loaded Signal parameters:")
print(f"  • Bar 2 Target: {bar2_time_str}")
print(f"  • Bar 3 Target: {bar3_time_str}")
print(f"  • Operator:     {operator}")
print("==================================================\n")

# Fetch 1-min candles for query_date
df = get_intraday_candles(dhan, "13", "IDX_I", "INDEX", query_date, query_date)
if df is None or df.empty:
    print(f"Could not retrieve Nifty 50 candle logs for {query_date}.")
    sys.exit(1)

query_date_obj = datetime.strptime(query_date, "%Y-%m-%d").date()
target_expiry = get_dynamic_expiry_date_for_date("Nifty 50", query_date_obj, "WEEKLY")
print(f"Calculated Expiry Target: {target_expiry.strftime('%d-%b-%Y') if target_expiry else 'None'}\n")

# Ensure sorted chronologically
df = df.sort_values('time').reset_index(drop=True)
df.set_index('time', inplace=True)

# Generate 5-min candles
df_5m = df.resample('5Min', closed='left', label='left').agg({
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last'
}).dropna()

# Target timestamps parsed dynamically
import re
b2_match = re.search(r'\b(\d{2}):(\d{2})\b', bar2_time_str)
b2_hour, b2_min = int(b2_match.group(1)), int(b2_match.group(2))
b2_target_time = time(b2_hour, b2_min)
b2_close_dt = datetime.combine(datetime.today(), b2_target_time) + timedelta(minutes=5)
b2_close_time = b2_close_dt.time()

b3_match = re.search(r'\b(\d{2}):(\d{2})\b', bar3_time_str)
b3_hour, b3_min = int(b3_match.group(1)), int(b3_match.group(2))
b3_target_time = time(b3_hour, b3_min)
b3_close_dt = datetime.combine(datetime.today(), b3_target_time) + timedelta(minutes=5)
b3_close_time = b3_close_dt.time()

exit_cutoff_time = time(15, 15)

morning_high = None
morning_low = None
afternoon_high = None
afternoon_low = None

trigger_candle_stamp = None
trigger_high = None
trigger_low = None
stop_loss_price = None
target_price = None

position = "NONE"
entry_time = None
entry_price = None

# Iterate through 1-minute bars
for timestamp, bar in df.iterrows():
    t_ist = timestamp.time()
    t_str = t_ist.strftime("%H:%M")
    
    # 1. Update Morning Range at 11:25
    if t_ist == b2_close_time and morning_high is None:
        # Get 11:20 5m candle
        target_ts = timestamp - timedelta(minutes=5)
        if target_ts in df_5m.index:
            morning_high = float(df_5m.loc[target_ts, 'high'])
            morning_low = float(df_5m.loc[target_ts, 'low'])
            print(f"[{t_str}] Morning Candle (11:20) Range set:")
            print(f"  • High: {morning_high:.2f}")
            print(f"  • Low:  {morning_low:.2f}")
            
    # 2. Update Afternoon Range at 14:10
    if t_ist == b3_close_time and afternoon_high is None:
        # Get 14:05 5m candle
        target_ts = timestamp - timedelta(minutes=5)
        if target_ts in df_5m.index:
            afternoon_high = float(df_5m.loc[target_ts, 'high'])
            afternoon_low = float(df_5m.loc[target_ts, 'low'])
            print(f"[{t_str}] Afternoon Candle (14:05) Range set:")
            print(f"  • High: {afternoon_high:.2f}")
            print(f"  • Low:  {afternoon_low:.2f}")

    active_high = afternoon_high if (afternoon_high is not None and t_ist >= b3_target_time) else morning_high
    active_low = afternoon_low if (afternoon_low is not None and t_ist >= b3_target_time) else morning_low

    # 3. Entry checking
    if position == "NONE" and active_high is not None and active_low is not None and t_ist < exit_cutoff_time:
        # Check if a 5-minute candle completed
        if t_ist.minute % 5 == 0:
            last_5m_ts = timestamp - timedelta(minutes=5)
            if last_5m_ts in df_5m.index:
                last_5m = df_5m.loc[last_5m_ts]
                close_val = float(last_5m['close'])
                
                # Check breakout
                if operator == "Buy on Dip" and close_val > active_high:
                    if trigger_candle_stamp != last_5m_ts:
                        trigger_candle_stamp = last_5m_ts
                        trigger_high = float(last_5m['high'])
                        trigger_low = None
                        stop_loss_price = active_low
                        targets = [trigger_high + (1.0 * i) * (trigger_high - stop_loss_price) for i in range(1, 6)]
                        target_price = targets[4]
                        active_target_index = 0
                        print(f"[{t_str}] [TRIGGER SET] 5-min candle ({last_5m_ts.strftime('%H:%M')}) closed above Range High. Trigger High: {trigger_high:.2f}")
                        print(f"  • Stop Loss: {stop_loss_price:.2f}")
                        print(f"  • Targets: T1={targets[0]:.2f} | T2={targets[1]:.2f} | T3={targets[2]:.2f} | T4={targets[3]:.2f} | T5={targets[4]:.2f}")
                        
                elif operator == "Sell on Rise" and close_val < active_low:
                    if trigger_candle_stamp != last_5m_ts:
                        trigger_candle_stamp = last_5m_ts
                        trigger_low = float(last_5m['low'])
                        trigger_high = None
                        stop_loss_price = active_high
                        targets = [trigger_low - (1.0 * i) * (stop_loss_price - trigger_low) for i in range(1, 6)]
                        target_price = targets[4]
                        active_target_index = 0
                        print(f"[{t_str}] [TRIGGER SET] 5-min candle ({last_5m_ts.strftime('%H:%M')}) closed below Range Low. Trigger Low: {trigger_low:.2f}")
                        print(f"  • Stop Loss: {stop_loss_price:.2f}")
                        print(f"  • Targets: T1={targets[0]:.2f} | T2={targets[1]:.2f} | T3={targets[2]:.2f} | T4={targets[3]:.2f} | T5={targets[4]:.2f}")

        # Check breakout entry trigger
        close_1m = float(bar['close'])
        if operator == "Buy on Dip" and trigger_high is not None:
            if close_1m > trigger_high:
                position = "LONG"
                entry_time = t_str
                entry_price = close_1m
                entry_timestamp = timestamp
                
                # Option contract details
                strike = get_atm_itm_strike("Nifty 50", active_high, "CE")
                opt_id, opt_sym = find_option_security_id("Nifty 50", target_expiry, strike, "CE")
                
                # Fetch option candles
                opt_df = get_intraday_candles(dhan, opt_id, "NSE_FNO", "OPTIDX", query_date, query_date)
                if opt_df is not None and not opt_df.empty:
                    opt_df.set_index('time', inplace=True)
                
                entry_opt_price = None
                if opt_df is not None and timestamp in opt_df.index:
                    entry_opt_price = float(opt_df.loc[timestamp, 'close'])
                else:
                    entry_opt_price = 100.0
                
                # Sizing calculation
                trade_lots, trade_qty = print_sizing_card(entry_price, stop_loss_price, entry_opt_price)
                
                print(f"[{t_str}] [ENTRY SIMULATED] Price {close_1m:.2f} crossed Trigger High {trigger_high:.2f}. BUY CALL Option {opt_sym} (ID: {opt_id}).")
                print(f"  • Sizing: {trade_lots} lots ({trade_qty} shares) @ {entry_opt_price:.2f}")
                
        elif operator == "Sell on Rise" and trigger_low is not None:
            if close_1m < trigger_low:
                position = "SHORT"
                entry_time = t_str
                entry_price = close_1m
                entry_timestamp = timestamp
                
                # Option contract details
                strike = get_atm_itm_strike("Nifty 50", active_low, "PE")
                opt_id, opt_sym = find_option_security_id("Nifty 50", target_expiry, strike, "PE")
                
                # Fetch option candles
                opt_df = get_intraday_candles(dhan, opt_id, "NSE_FNO", "OPTIDX", query_date, query_date)
                if opt_df is not None and not opt_df.empty:
                    opt_df.set_index('time', inplace=True)
                
                entry_opt_price = None
                if opt_df is not None and timestamp in opt_df.index:
                    entry_opt_price = float(opt_df.loc[timestamp, 'close'])
                else:
                    entry_opt_price = 100.0
                
                # Sizing calculation
                trade_lots, trade_qty = print_sizing_card(entry_price, stop_loss_price, entry_opt_price)
                
                print(f"[{t_str}] [ENTRY SIMULATED] Price {close_1m:.2f} crossed Trigger Low {trigger_low:.2f}. BUY PUT Option {opt_sym} (ID: {opt_id}).")
                print(f"  • Sizing: {trade_lots} lots ({trade_qty} shares) @ {entry_opt_price:.2f}")

    # 4. Exit checking
    elif position != "NONE":
        close_1m = float(bar['close'])
        high_1m = float(bar['high'])
        low_1m = float(bar['low'])
        
        # Trailing SL check (on 5-min candle close)
        if t_ist.minute % 5 == 0:
            last_5m_ts = timestamp - timedelta(minutes=5)
            if last_5m_ts in df_5m.index:
                close_5m = float(df_5m.loc[last_5m_ts, 'close'])
                
                new_active_idx = active_target_index
                for i in range(active_target_index, 4): # Check T1 to T4
                    target_val = float(targets[i])
                    if position == "LONG":
                        if close_5m >= target_val:
                            new_active_idx = i + 1
                    elif position == "SHORT":
                        if close_5m <= target_val:
                            new_active_idx = i + 1
                            
                if new_active_idx > active_target_index:
                    if new_active_idx == 1:
                        new_sl = entry_price
                    else:
                        new_sl = float(targets[new_active_idx - 2])
                    
                    active_target_index = new_active_idx
                    stop_loss_price = new_sl
                    
                    print(f"[{t_str}] [TRAILING SL ACTION] 5-min candle closed at {close_5m:.2f} beyond T{new_active_idx}.")
                    print(f"  • SL shifted to:     {stop_loss_price:.2f}")
                    
        # A. Target Check (real-time high/low basis)
        if position == "LONG" and high_1m >= target_price:
            exit_opt_price = None
            if opt_df is not None and timestamp in opt_df.index:
                exit_opt_price = float(opt_df.loc[timestamp, 'close'])
            pnl = (exit_opt_price - entry_opt_price) if (exit_opt_price is not None and entry_opt_price is not None) else None
            
            print(f"[{t_str}] [EXIT TARGET MET] Nifty high {high_1m:.2f} >= Target {target_price:.2f}. Index Profit: {target_price - entry_price:.2f} points.")
            if pnl is not None:
                print(f"  • Option SELL premium: {exit_opt_price:.2f}. Option PnL: {pnl:+.2f} points.")
                print(f"  • Net Trade PnL: {pnl * trade_qty:+.2f} INR")
            
            position = "NONE"
            trigger_high = None
            trigger_low = None
            opt_df = None
            
        elif position == "SHORT" and low_1m <= target_price:
            exit_opt_price = None
            if opt_df is not None and timestamp in opt_df.index:
                exit_opt_price = float(opt_df.loc[timestamp, 'close'])
            pnl = (exit_opt_price - entry_opt_price) if (exit_opt_price is not None and entry_opt_price is not None) else None
            
            print(f"[{t_str}] [EXIT TARGET MET] Nifty low {low_1m:.2f} <= Target {target_price:.2f}. Index Profit: {entry_price - target_price:.2f} points.")
            if pnl is not None:
                print(f"  • Option SELL premium: {exit_opt_price:.2f}. Option PnL: {pnl:+.2f} points.")
                print(f"  • Net Trade PnL: {pnl * trade_qty:+.2f} INR")
                
            position = "NONE"
            trigger_high = None
            trigger_low = None
            opt_df = None
            
        # B. Stop Loss Check (5-min close basis)
        elif position != "NONE" and t_ist.minute % 5 == 0:
            last_5m_ts = timestamp - timedelta(minutes=5)
            if last_5m_ts in df_5m.index:
                close_5m = float(df_5m.loc[last_5m_ts, 'close'])
                if position == "LONG" and close_5m < stop_loss_price:
                    exit_opt_price = None
                    if opt_df is not None and timestamp in opt_df.index:
                        exit_opt_price = float(opt_df.loc[timestamp, 'close'])
                    pnl = (exit_opt_price - entry_opt_price) if (exit_opt_price is not None and entry_opt_price is not None) else None
                    
                    print(f"[{t_str}] [EXIT STOP LOSS MET] 5-min candle closed at {close_5m:.2f} below SL {stop_loss_price:.2f}. Index Loss: {entry_price - close_5m:.2f} points.")
                    if pnl is not None:
                        print(f"  • Option SELL premium: {exit_opt_price:.2f}. Option PnL: {pnl:+.2f} points.")
                        print(f"  • Net Trade PnL: {pnl * trade_qty:+.2f} INR")
                    
                    position = "NONE"
                    trigger_high = None
                    trigger_low = None
                    opt_df = None
                elif position == "SHORT" and close_5m > stop_loss_price:
                    exit_opt_price = None
                    if opt_df is not None and timestamp in opt_df.index:
                        exit_opt_price = float(opt_df.loc[timestamp, 'close'])
                    pnl = (exit_opt_price - entry_opt_price) if (exit_opt_price is not None and entry_opt_price is not None) else None
                    
                    print(f"[{t_str}] [EXIT STOP LOSS MET] 5-min candle closed at {close_5m:.2f} above SL {stop_loss_price:.2f}. Index Loss: {close_5m - entry_price:.2f} points.")
                    if pnl is not None:
                        print(f"  • Option SELL premium: {exit_opt_price:.2f}. Option PnL: {pnl:+.2f} points.")
                        print(f"  • Net Trade PnL: {pnl * trade_qty:+.2f} INR")
                    
                    position = "NONE"
                    trigger_high = None
                    trigger_low = None
                    opt_df = None
                    
        # C. Universal Time Exit
        elif position != "NONE" and t_ist >= exit_cutoff_time:
            exit_opt_price = None
            if opt_df is not None and timestamp in opt_df.index:
                exit_opt_price = float(opt_df.loc[timestamp, 'close'])
            pnl = (exit_opt_price - entry_opt_price) if (exit_opt_price is not None and entry_opt_price is not None) else None
            
            index_pnl = (close_1m - entry_price) if position == "LONG" else (entry_price - close_1m)
            print(f"[{t_str}] [EXIT TIME LIMIT] Universal Time Exit reached (15:15). Close at {close_1m:.2f}. Index PnL: {index_pnl:+.2f} points.")
            if pnl is not None:
                print(f"  • Option SELL premium: {exit_opt_price:.2f}. Option PnL: {pnl:+.2f} points.")
                print(f"  • Net Trade PnL: {pnl * trade_qty:+.2f} INR")
                
            position = "NONE"
            trigger_high = None
            trigger_low = None
            opt_df = None

import sys
import time
import os
import json
import re
import threading
from datetime import datetime, timedelta
from dhanhq import dhanhq, DhanLogin, MarketFeed
from dhanhq.dhan_context import DhanContext

def custom_thread_excepthook(args):
    exc_type = args.exc_type
    exc_value = args.exc_value
    if exc_type is not None and "websockets.exceptions" in str(exc_type) and "HTTP 429" in str(exc_value):
        from utils import safe_print
        safe_print("\n[INFO] Live feed WebSocket connection limit reached (HTTP 429). REST API polling will be used.")
        return
    threading.__excepthook__(args)

threading.excepthook = custom_thread_excepthook

# Import modular helper components
from utils import (
    ENV_FILE, IST, safe_print, get_totp_token, get_index_metadata,
    get_state_filename, save_trade_state, acquire_lock, DATA_DIR,
    is_indicator_strategy_enabled, get_indicator_state_filename
)
from broker import (
    get_dhan_security_id, get_dynamic_expiry_date, get_current_price,
    check_and_get_time_candle, is_token_valid, authenticate_and_get_dhan_client,
    DhanTokenExpiredException
)

# New modular refactoring layers
from trade_state import load_and_verify_active_state, check_for_scraper_signals
from trade_rules import (
    is_within_trading_hours, is_signal_valid_for_today,
    extract_target_times, evaluate_exit_conditions, get_sleep_interval,
    calculate_trade_levels, check_and_update_trailing_sl
)
from trade_engine import (
    update_range_candles, execute_entry_trade, execute_exit_trade
)

# Indicator Strategy module
from indicator_rules import (
    load_indicator_state, check_indicator_signal,
    evaluate_indicator_entry, evaluate_indicator_exit
)

# ==============================================================================
# TARGET UNDERLYING INDEX CONFIG
# ==============================================================================
UNDERLYING = sys.argv[1] if len(sys.argv) > 1 else "Nifty Bank"
# Target index options: "Nifty 50", "Nifty Bank", "S&P BSE Sensex", "Fin Nifty"

# Global variables for real-time WebSocket tick prices
current_index_ltp = None
current_option_ltp = None        # TIME MACHINE active option LTP
ind_option_ltp = None            # INDICATOR strategy active option LTP
last_tick_time = None

# Populated after state load — used to route option ticks correctly
_tm_sec_id = None    # TIME MACHINE option security id
_ind_sec_id = None   # INDICATOR option security id

def on_ticks(feed_inst, tick_data):
    """WebSocket tick callback — routes index and option LTPs to correct globals."""
    global current_index_ltp, current_option_ltp, ind_option_ltp, last_tick_time
    global _tm_sec_id, _ind_sec_id
    if isinstance(tick_data, dict):
        segment = tick_data.get("exchange_segment")
        ltp_str = tick_data.get("LTP")
        sec_id_tick = str(tick_data.get("security_id", ""))
        if ltp_str:
            try:
                ltp = float(ltp_str)
                # exchange_segment 0: IDX_I (index), 2: NSE_FNO (options), 8: BSE_FNO
                if segment == 0:
                    current_index_ltp = ltp
                    last_tick_time = time.time()
                elif segment in (2, 8):
                    last_tick_time = time.time()
                    # Route to the correct option LTP by security ID
                    if _ind_sec_id and sec_id_tick == str(_ind_sec_id):
                        ind_option_ltp = ltp
                    elif _tm_sec_id and sec_id_tick == str(_tm_sec_id):
                        current_option_ltp = ltp
                    else:
                        # Fallback: if only one strategy is active, assign to it
                        if _tm_sec_id and not _ind_sec_id:
                            current_option_ltp = ltp
                        elif _ind_sec_id and not _tm_sec_id:
                            ind_option_ltp = ltp
                        else:
                            current_option_ltp = ltp
            except Exception:
                pass



def main():
    global current_index_ltp, current_option_ltp, ind_option_ltp
    global _tm_sec_id, _ind_sec_id
    
    if not acquire_lock(UNDERLYING):
        safe_print(f"[ERROR] Another instance is already running for {UNDERLYING}. Exiting to prevent conflict.")
        sys.exit(1)
        
    safe_print("==================================================")
    safe_print(" TradingView Visual Strategy Execution Agent")
    safe_print("==================================================")
    
    # Load credentials from .env JSON
    if not os.path.exists(ENV_FILE):
        safe_print(f"[ERROR] Config file {ENV_FILE} is missing. Please place it in the project root.")
        sys.exit(1)
        
    try:
        with open(ENV_FILE, "r") as f:
            config = json.load(f)
            dhan_config = config.get("dhan_config", {})
            client_code = dhan_config.get("client_code")
            totp_secret = dhan_config.get("totp_secret")
            pin = dhan_config.get("pin")
    except Exception as e:
        safe_print(f"[ERROR] Failed to parse {ENV_FILE} file: {e}")
        sys.exit(1)
        
    if not client_code or not totp_secret or not pin:
        safe_print("[ERROR] Credentials client_code, totp_secret, or pin missing in .env json config.")
        sys.exit(1)
        
    # Get dynamic TOTP code and authorize with Dhan SDK with retry logic
    dhan, access_token = authenticate_and_get_dhan_client(client_code, totp_secret, pin)
    
    safe_print("Dhan Authentication Successful! Access token generated.")
    
    # Initialize the main SDK client context
    context = DhanContext(client_code, access_token)
    dhan = dhanhq(context)
    
    index_sec_id = get_dhan_security_id(UNDERLYING)
    safe_print(f"Underlying: {UNDERLYING} (Security ID: {index_sec_id})")
    
    # Fetch index metadata (includes lotSize, strikeStep, symbol, expiryStyle)
    meta = get_index_metadata(UNDERLYING)
    expiry_style = meta["expiryStyle"]
    
    # Load TIME MACHINE session state
    state_file = get_state_filename(UNDERLYING)
    state = load_and_verify_active_state(dhan, UNDERLYING, state_file)

    # Load INDICATOR strategy session state
    ind_state_file = get_indicator_state_filename(UNDERLYING)
    ind_state = load_indicator_state(ind_state_file, UNDERLYING)
    if is_indicator_strategy_enabled():
        safe_print("[INDICATOR] Strategy is ENABLED (indicator_strategy_enabled=true in .env)")
    else:
        safe_print("[INDICATOR] Strategy is DISABLED (indicator_strategy_enabled=false in .env)")
    
    # Path to scraper output file
    sanitized_underlying = re.sub(r'[^A-Za-z0-9]', '_', UNDERLYING)
    scrape_file = os.path.join(DATA_DIR, f"scrap_{sanitized_underlying}.json")
    
    # Recover missing signal details if state has a timestamp
    if state.get("LastSignalTime") and (not state.get("Bar2Time") or not state.get("Bar3Time")):
        if os.path.exists(scrape_file):
            try:
                with open(scrape_file, "r") as f:
                    data = json.load(f)
                    if data:
                        last_entry = data[-1]
                        if last_entry.get("Timestamp") == state["LastSignalTime"]:
                            state["Bar2Time"] = last_entry.get("Bar 2 Time")
                            state["Bar3Time"] = last_entry.get("Bar 3 Time")
                            state["Operator"] = last_entry.get("Operator")
            except Exception:
                pass

    # Determine dynamic expiry target
    target_expiry = None
    if state.get("ExpiryDate"):
        target_expiry = datetime.strptime(state["ExpiryDate"], "%Y-%m-%d").date()
    else:
        target_expiry = get_dynamic_expiry_date(UNDERLYING, expiry_style)
        if target_expiry:
            state["ExpiryDate"] = target_expiry.strftime("%Y-%m-%d")
            save_trade_state(state_file, state)
            
    if target_expiry is None:
        safe_print("[ERROR] Could not calculate dynamic expiry date from Scrip Master.")
        sys.exit(1)
        
    safe_print(f"Dynamic Expiry Target (Theta Protection): {target_expiry.strftime('%d-%b-%Y')} ({expiry_style})")
    
    safe_print("Connecting to live WebSocket tick feed...")
    opt_seg = 8 if "Sensex" in UNDERLYING or "BSE" in UNDERLYING else 2
    try:
        feed = MarketFeed(context, [(0, str(index_sec_id))], version='v2', on_ticks=on_ticks)
        feed.start()
        # TIME MACHINE: subscribe to F&O ticks if starting with active position
        if state["Position"] != "NONE" and state["SecurityId"]:
            _tm_sec_id = state["SecurityId"]
            feed.subscribe_symbols([(opt_seg, str(state["SecurityId"]))])
        # INDICATOR: subscribe to F&O ticks if resuming active indicator position
        if ind_state.get("Position") != "NONE" and ind_state.get("SecurityId"):
            _ind_sec_id = ind_state["SecurityId"]
            feed.subscribe_symbols([(opt_seg, str(ind_state["SecurityId"]))])
    except Exception as e:
        safe_print(f"[WARNING] Could not start WebSocket feed: {e}. Falling back to REST API polling.")
        feed = None
        
    safe_print(f"Monitoring scrape output: {scrape_file}...")
    
    try:
        while True:
            try:
                # If we haven't received a WebSocket tick for more than 15 seconds,
                # clear the cache to force a fresh REST API check.
                if last_tick_time is not None and (time.time() - last_tick_time) > 15:
                    current_index_ltp = None
                    current_option_ltp = None
                    ind_option_ltp = None
                    
                # 1. Fetch Index LTP (WebSocket with REST fallback)
                ltp = current_index_ltp if current_index_ltp is not None else get_current_price(dhan, index_sec_id)
                if ltp is None:
                    # If LTP is None, verify token validity. If invalid, trigger re-authentication.
                    if not is_token_valid(dhan, index_sec_id):
                        safe_print("[WARNING] Active Dhan token is invalid. Re-authenticating...")
                        try:
                            if feed is not None:
                                feed.close_connection()
                        except Exception:
                            pass
                        dhan, access_token = authenticate_and_get_dhan_client(client_code, totp_secret, pin, force_refresh=True, current_token=access_token)
                        context = DhanContext(client_code, access_token)
                        try:
                            feed = MarketFeed(context, [(0, str(index_sec_id))], version='v2', on_ticks=on_ticks)
                            feed.start()
                            if state["Position"] != "NONE" and state["SecurityId"]:
                                _tm_sec_id = state["SecurityId"]
                                feed.subscribe_symbols([(opt_seg, str(state["SecurityId"]))])
                            if ind_state.get("Position") != "NONE" and ind_state.get("SecurityId"):
                                _ind_sec_id = ind_state["SecurityId"]
                                feed.subscribe_symbols([(opt_seg, str(ind_state["SecurityId"]))])
                        except Exception as e:
                            safe_print(f"[WARNING] Could not restart WebSocket feed: {e}. Falling back to REST API polling.")
                            feed = None
                        safe_print("Re-authentication complete. Resuming...")
                        continue
                    
                    safe_print("[ERROR] Error retrieving Index price. Retrying in 5 seconds...")
                    time.sleep(5)
                    continue
                    
                # Check current time in IST
                now_ist = datetime.now(IST)
                now_time_str = now_ist.strftime("%H:%M")
                now_date_str = now_ist.strftime("%d %b")
                
                # Strict trading hours constraint
                if not is_within_trading_hours(now_ist):
                    safe_print(f"[{now_time_str}] Outside trading hours (09:15 - 15:30). Sleeping...")
                    time.sleep(300)
                    continue
                    
                # 2. Check for updated signals in the JSON scrape file
                new_signal_detected = check_for_scraper_signals(scrape_file, state, expiry_style)
                if new_signal_detected:
                    # Reload target expiry if updated
                    if state.get("ExpiryDate"):
                        target_expiry = datetime.strptime(state["ExpiryDate"], "%Y-%m-%d").date()
                    save_trade_state(state_file, state)
                    safe_print(f"\n[NEW SIGNAL LOADED] Timestamp: {state['LastSignalTime']}")
                    safe_print(f"  • Bar 2 Target: {state.get('Bar2Time')}")
                    safe_print(f"  • Bar 3 Target: {state.get('Bar3Time')}")
                    safe_print(f"  • Operator:     {state.get('Operator')}")
                    safe_print(f"  • Stop Loss:    {state.get('StopLossPrice') or '--'}")
                    safe_print(f"  • Target:       {state.get('TargetPrice') or '--'}")
                    safe_print(f"  • Expiry Target:{target_expiry.strftime('%d-%b-%Y') if target_expiry else '--'}")
                    
                    # Send Telegram notification
                    msg = (
                        f"🔔 <b>[NEW SIGNAL LOADED]</b>\n"
                        f"• <b>Underlying:</b> {UNDERLYING}\n"
                        f"• <b>Operator:</b> {state.get('Operator')}\n"
                        f"• <b>Morning:</b> {state.get('Bar2Time')}\n"
                        f"• <b>Afternoon:</b> {state.get('Bar3Time')}\n"
                        f"• <b>Expiry Target:</b> {target_expiry.strftime('%d-%b-%Y') if target_expiry else '--'}\n"
                        f"• <b>Timestamp:</b> {state.get('LastSignalTime')}"
                    )
                    try:
                        from utils import send_telegram_notification
                        send_telegram_notification(msg)
                    except Exception as telegram_err:
                        safe_print(f"[WARNING] Telegram notification failed: {telegram_err}")
                    
                bar2_time_str = state.get("Bar2Time")
                bar3_time_str = state.get("Bar3Time")
                operator_val = state.get("Operator")
                
                if not bar2_time_str or not bar3_time_str:
                    safe_print(f"[{now_time_str}] No valid visual signals available. Waiting...")
                    time.sleep(10)
                    continue
                    
                # Safety check: Scraper signal date must match today's date
                if not is_signal_valid_for_today(bar2_time_str, now_date_str) and not new_signal_detected:
                    safe_print(f"[{now_time_str}] Loaded signal date ({bar2_time_str}) is not today ({now_date_str}). Waiting for scraper update...")
                    time.sleep(15)
                    continue
                    
                # Extract target times
                b2_t, b2_close_t, b3_t, b3_close_t = extract_target_times(bar2_time_str, bar3_time_str)
                if not b2_t:
                    time.sleep(10)
                    continue
                    
                # Clear morning trigger candle if cycle reset time (b3_t) has been reached
                if b3_t and now_time_str >= b3_t:
                    if state.get("TriggerCandleStamp") and state.get("TriggerCandleStamp") < b3_t:
                        state["TriggerCandleStamp"] = None
                        state["TriggerCandleHigh"] = None
                        state["TriggerCandleLow"] = None
                        state["StopLossPrice"] = None
                        state["TargetPrice"] = None
                        # Reset per-cycle re-entry flags for the afternoon session
                        state["TMReEntryUsed"] = False
                        state["TMLastExitReason"] = None
                        save_trade_state(state_file, state)
                        safe_print(f"[{now_time_str}] Morning trigger candle cleared at cycle reset.")
                    
                # If we are waiting for the morning range candle to start
                if state["Position"] == "NONE" and state["MorningHigh"] is None:
                    try:
                        b2_hour, b2_min = map(int, b2_t.split(":"))
                        target_time = now_ist.replace(hour=b2_hour, minute=b2_min, second=0, microsecond=0)
                        if target_time > now_ist:
                            sleep_sec = (target_time - now_ist).total_seconds() - 30
                            if sleep_sec > 15:
                                safe_print(f"[{now_time_str}] Morning Range starts at {b2_t}. Sleeping {int(sleep_sec)} seconds until then...")
                                time.sleep(sleep_sec)
                                continue
                    except Exception as e:
                        safe_print(f"[WARNING] Error calculating sleep duration until morning range: {e}")
                    
                # 3. SET RANGE CANDLES
                state_changed = update_range_candles(
                    dhan, index_sec_id, state, b2_t, b2_close_t, b3_t, b3_close_t,
                    now_time_str, target_expiry, UNDERLYING
                )
                if state_changed:
                    save_trade_state(state_file, state)
                    
                active_high = state["AfternoonHigh"] if (state["AfternoonHigh"] is not None and now_time_str >= b3_t) else state["MorningHigh"]
                active_low = state["AfternoonLow"] if (state["AfternoonLow"] is not None and now_time_str >= b3_t) else state["MorningLow"]
                
                # 4. TRADING EXECUTION STRATEGY
                if state["Position"] == "NONE":
                    if active_high is not None and active_low is not None and now_time_str < "15:15":
                        # Check for TriggerCandle breakout close
                        time_candle_str = state.get("Bar3Time") if (state["AfternoonHigh"] is not None and now_time_str >= b3_t) else state.get("Bar2Time")
                        breakout_val, stamp, api_success = check_and_get_time_candle(
                            dhan, index_sec_id, active_high, active_low, operator_val, time_candle_str
                        )
                        
                        if api_success:
                            if breakout_val:
                                if stamp != state.get("TriggerCandleStamp"):
                                    state["TriggerCandleStamp"] = stamp
                                    if operator_val == "Buy on Dip":
                                        state["TriggerCandleHigh"] = breakout_val
                                        state["TriggerCandleLow"] = None
                                        
                                        # Determine SL and Target dynamically
                                        sl, targets = calculate_trade_levels(operator_val, breakout_val, active_low)
                                        state["StopLossPrice"] = sl
                                        state["Targets"] = targets
                                        state["TargetPrice"] = targets[4]  # T5 is the final exit target
                                        state["ActiveTargetIndex"] = 0
                                        
                                        safe_print(f"[{now_time_str}] [TRIGGER CANDLE SET] 5-min candle closed above Range High at {stamp}. Trigger High: {breakout_val}")
                                        safe_print(f"  • Calculated Stop Loss (Range Low): {sl:.2f}")
                                        safe_print(f"  • Calculated Targets: T1={targets[0]:.2f} | T2={targets[1]:.2f} | T3={targets[2]:.2f} | T4={targets[3]:.2f} | T5={targets[4]:.2f}")
                                    else:
                                        state["TriggerCandleLow"] = breakout_val
                                        state["TriggerCandleHigh"] = None
                                        
                                        # Determine SL and Target dynamically
                                        sl, targets = calculate_trade_levels(operator_val, breakout_val, active_high)
                                        state["StopLossPrice"] = sl
                                        state["Targets"] = targets
                                        state["TargetPrice"] = targets[4]  # T5 is the final exit target
                                        state["ActiveTargetIndex"] = 0
                                        
                                        safe_print(f"[{now_time_str}] [TRIGGER CANDLE SET] 5-min candle closed below Range Low at {stamp}. Trigger Low: {breakout_val}")
                                        safe_print(f"  • Calculated Stop Loss (Range High): {sl:.2f}")
                                        safe_print(f"  • Calculated Targets: T1={targets[0]:.2f} | T2={targets[1]:.2f} | T3={targets[2]:.2f} | T4={targets[3]:.2f} | T5={targets[4]:.2f}")
                                        
                                    save_trade_state(state_file, state)
                            else:
                                # Clear trigger levels from state if trigger candle is cancelled/cleared
                                if state.get("TriggerCandleStamp") is not None:
                                    state["TriggerCandleStamp"] = None
                                    state["TriggerCandleHigh"] = None
                                    state["TriggerCandleLow"] = None
                                    state["StopLossPrice"] = None
                                    state["TargetPrice"] = None
                                    safe_print(f"[{now_time_str}] [TRIGGER CANDLE RESET/CANCELLED] Trigger candle cleared.")
                                    save_trade_state(state_file, state)
                        else:
                            safe_print(f"[{now_time_str}] [WARNING] Candle range evaluation skipped due to API failure. Retaining existing trigger candle state.")
                            
                        # Check breakout crossings for Entry (Position Candle)
                        if operator_val == "Buy on Dip" and state.get("TriggerCandleHigh") is not None:
                            if ltp > state["TriggerCandleHigh"]:
                                if state.get("TriggerCandleStamp") != state.get("TradedTriggerStamp"):
                                    success = execute_entry_trade(
                                        dhan, UNDERLYING, state, "CE", ltp, client_code, feed,
                                        target_expiry, active_high, active_low, current_option_ltp
                                    )
                                    if success:
                                        # Record operator at entry time for re-entry direction guard
                                        state["TMEntryOperator"] = operator_val
                                        state["TradedTriggerStamp"] = state.get("TriggerCandleStamp")
                                        save_trade_state(state_file, state)
                                elif (
                                    state.get("TMLastExitReason") in ("SL", "MAXLOSS")
                                    and not state.get("TMReEntryUsed", False)
                                    and operator_val == state.get("TMEntryOperator")  # direction unchanged
                                ):
                                    # Allow one re-entry per cycle after a stop-loss or max-loss exit,
                                    # only if the signal direction has not flipped since the original entry.
                                    safe_print(f"[{now_time_str}] [TM RE-ENTRY] Re-entering after {state.get('TMLastExitReason')} exit (operator unchanged: {operator_val}).")
                                    success = execute_entry_trade(
                                        dhan, UNDERLYING, state, "CE", ltp, client_code, feed,
                                        target_expiry, active_high, active_low, current_option_ltp
                                    )
                                    if success:
                                        state["TMReEntryUsed"] = True
                                        state["TradedTriggerStamp"] = state.get("TriggerCandleStamp")
                                        save_trade_state(state_file, state)
                                    
                        elif operator_val == "Sell on Rise" and state.get("TriggerCandleLow") is not None:
                            if ltp < state["TriggerCandleLow"]:
                                if state.get("TriggerCandleStamp") != state.get("TradedTriggerStamp"):
                                    success = execute_entry_trade(
                                        dhan, UNDERLYING, state, "PE", ltp, client_code, feed,
                                        target_expiry, active_low, active_high, current_option_ltp
                                    )
                                    if success:
                                        # Record operator at entry time for re-entry direction guard
                                        state["TMEntryOperator"] = operator_val
                                        state["TradedTriggerStamp"] = state.get("TriggerCandleStamp")
                                        save_trade_state(state_file, state)
                                elif (
                                    state.get("TMLastExitReason") in ("SL", "MAXLOSS")
                                    and not state.get("TMReEntryUsed", False)
                                    and operator_val == state.get("TMEntryOperator")  # direction unchanged
                                ):
                                    # Allow one re-entry per cycle after a stop-loss or max-loss exit,
                                    # only if the signal direction has not flipped since the original entry.
                                    safe_print(f"[{now_time_str}] [TM RE-ENTRY] Re-entering after {state.get('TMLastExitReason')} exit (operator unchanged: {operator_val}).")
                                    success = execute_entry_trade(
                                        dhan, UNDERLYING, state, "PE", ltp, client_code, feed,
                                        target_expiry, active_low, active_high, current_option_ltp
                                    )
                                    if success:
                                        state["TMReEntryUsed"] = True
                                        state["TradedTriggerStamp"] = state.get("TriggerCandleStamp")
                                        save_trade_state(state_file, state)
                else:
                    # Check and update trailing Stop Loss
                    if check_and_update_trailing_sl(state, dhan, index_sec_id, state["Position"]):
                        save_trade_state(state_file, state)
                        
                    # 5. EXIT CONDITIONS
                    should_exit, exit_reason = evaluate_exit_conditions(state, ltp, now_time_str, b3_t, dhan, index_sec_id, option_ltp=current_option_ltp)
                    if should_exit:
                        safe_print(f"\n[EXIT SIGNAL] {exit_reason}. Closing position...")
                        success = execute_exit_trade(
                            dhan, UNDERLYING, state, ltp, client_code, feed,
                            target_expiry, active_high, active_low,
                            exit_reason=exit_reason
                        )
                        if success:
                            # Categorize exit reason for TM re-entry eligibility
                            reason_upper = exit_reason.upper()
                            if "STOP LOSS" in reason_upper or "STOP_LOSS" in reason_upper:
                                state["TMLastExitReason"] = "SL"
                            elif "MAX CAPITAL RISK" in reason_upper:
                                state["TMLastExitReason"] = "MAXLOSS"
                            elif "TARGET" in reason_upper:
                                state["TMLastExitReason"] = "TARGET"
                            else:
                                state["TMLastExitReason"] = "TIME"
                            # Clear max-loss breach counter on exit
                            state["MaxLossBreachCount"] = 0
                            _tm_sec_id = None
                            save_trade_state(state_file, state)

                # ============================================================
                # INDICATOR STRATEGY BLOCK
                # ============================================================
                if is_indicator_strategy_enabled():
                    # Check for new/updated indicator signal
                    ind_signal_detected = check_indicator_signal(scrape_file, ind_state, target_expiry)
                    if ind_signal_detected:
                        save_trade_state(ind_state_file, ind_state)
                        try:
                            from utils import send_telegram_notification
                            ind_entry = ind_state.get('SignalEntry') or '--'
                            ind_sl = ind_state.get('SignalSL') or '--'
                            ind_tgts = ind_state.get('Targets') or []
                            tgt_str = " | ".join(f"T{i+1}={v:.2f}" for i, v in enumerate(ind_tgts)) if ind_tgts else '--'
                            ind_msg = (
                                f"\U0001f4cc <b>[INDICATOR] New Signal</b>\n"
                                f"\u2022 <b>Underlying:</b> {UNDERLYING}\n"
                                f"\u2022 <b>Operator:</b> {ind_state.get('Operator')}\n"
                                f"\u2022 <b>ENTRY:</b> {ind_entry}\n"
                                f"\u2022 <b>SL:</b> {ind_sl}\n"
                                f"\u2022 <b>Targets:</b> {tgt_str}\n"
                                f"\u2022 <b>Timestamp:</b> {ind_state.get('LastSignalTime')}"
                            )
                            send_telegram_notification(ind_msg)
                        except Exception as tg_err:
                            safe_print(f"[WARNING] Telegram notification failed: {tg_err}")

                    if ind_state.get("Position") == "NONE":
                        # Check entry condition
                        ind_should_enter, ind_opt_type = evaluate_indicator_entry(ind_state, ltp, now_time_str)
                        if ind_should_enter:
                            safe_print(f"[{now_time_str}] [INDICATOR] Entry triggered: LTP={ltp:.2f} | ENTRY={ind_state.get('SignalEntry')} | {ind_opt_type}")
                            # Determine active and opposite range values for sizing
                            if ind_opt_type == "CE":
                                ind_active_range = ind_state.get("SignalEntry")
                                ind_opp_range = ind_state.get("SignalSL")
                            else:
                                ind_active_range = ind_state.get("SignalEntry")
                                ind_opp_range = ind_state.get("SignalSL")
                            ind_success = execute_entry_trade(
                                dhan, UNDERLYING, ind_state, ind_opt_type, ltp, client_code, feed,
                                target_expiry, ind_active_range, ind_opp_range, ind_option_ltp,
                                strategy_tag="INDICATOR"
                            )
                            if ind_success:
                                _ind_sec_id = ind_state.get("SecurityId")
                                # Subscribe feed to indicator option ticks
                                if feed is not None and _ind_sec_id:
                                    try:
                                        feed.subscribe_symbols([(opt_seg, str(_ind_sec_id))])
                                    except Exception as e:
                                        safe_print(f"[INDICATOR] Error subscribing to option feed: {e}")
                                ind_state["TradedSignalTime"] = ind_state.get("LastSignalTime")
                                save_trade_state(ind_state_file, ind_state)
                    else:
                        # Check and update trailing SL for indicator position
                        if check_and_update_trailing_sl(ind_state, dhan, index_sec_id, ind_state["Position"]):
                            save_trade_state(ind_state_file, ind_state)

                        # Check exit conditions
                        ind_should_exit, ind_exit_reason = evaluate_indicator_exit(
                            ind_state, ltp, now_time_str, dhan, index_sec_id, option_ltp=ind_option_ltp
                        )
                        if ind_should_exit:
                            safe_print(f"\n{ind_exit_reason}. Closing indicator position...")
                            ind_exit_success = execute_exit_trade(
                                dhan, UNDERLYING, ind_state, ltp, client_code, feed,
                                target_expiry, ind_state.get("SignalEntry"), ind_state.get("SignalSL"),
                                strategy_tag="INDICATOR", exit_reason=ind_exit_reason
                            )
                            if ind_exit_success:
                                _ind_sec_id = None
                                ind_option_ltp = None
                                save_trade_state(ind_state_file, ind_state)
                # ============================================================
                # END INDICATOR STRATEGY BLOCK
                # ============================================================
                            
                # 6. DYNAMIC LOOP TIMING
                # Use shortest sleep so both strategies stay responsive
                either_active = state["Position"] != "NONE" or ind_state.get("Position") != "NONE"
                active_pos = state["Position"] if state["Position"] != "NONE" else (ind_state.get("Position") or "NONE")
                sleep_sec, next_time = get_sleep_interval(active_pos, now_ist)
                
                if state["Position"] == "NONE":
                    tc_display = state.get("TriggerCandleHigh") or state.get("TriggerCandleLow") or '--'
                    ind_pos_display = ind_state.get('Position', 'NONE')
                    ind_entry_display = ind_state.get('SignalEntry') or '--'
                    safe_print(
                        f"[{now_time_str}] LTP: {ltp:.2f} | TM Pos: {state['Position']} | TC: {tc_display} | "
                        f"IND Pos: {ind_pos_display} | IND Entry: {ind_entry_display}"
                    )
                    safe_print(f"  Sleeping {int(sleep_sec)} seconds to turn of 5-min candle ({next_time.strftime('%H:%M')})...")
                    
                time.sleep(sleep_sec)
                
            except DhanTokenExpiredException as token_err:
                safe_print(f"\n[WARNING] Dhan access token has expired or been invalidated: {token_err}. Re-authenticating...")
                try:
                    if feed is not None:
                        feed.close_connection()
                except Exception:
                    pass
                dhan, access_token = authenticate_and_get_dhan_client(client_code, totp_secret, pin, force_refresh=True, current_token=access_token)
                context = DhanContext(client_code, access_token)
                try:
                    feed = MarketFeed(context, [(0, str(index_sec_id))], version='v2', on_ticks=on_ticks)
                    feed.start()
                    if state["Position"] != "NONE" and state["SecurityId"]:
                        _tm_sec_id = state["SecurityId"]
                        feed.subscribe_symbols([(opt_seg, str(state["SecurityId"]))])
                    if ind_state.get("Position") != "NONE" and ind_state.get("SecurityId"):
                        _ind_sec_id = ind_state["SecurityId"]
                        feed.subscribe_symbols([(opt_seg, str(ind_state["SecurityId"]))])
                except Exception as e:
                    safe_print(f"[WARNING] Could not restart WebSocket feed after re-auth: {e}. Falling back to REST API polling.")
                    feed = None
                safe_print("Re-authentication complete. Resuming...")
                continue
            
    except KeyboardInterrupt:
        safe_print("\nStopping visual strategy trading agent...")
        try:
            if feed is not None:
                feed.close_connection()
        except Exception:
            pass

if __name__ == "__main__":
    main()

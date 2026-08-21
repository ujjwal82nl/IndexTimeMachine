import os
import json
from datetime import datetime
from utils import IST, safe_print, load_trade_state, save_trade_state

def get_default_state(underlying):
    """Returns a clean default session state dictionary."""
    return {
        "Date": datetime.now(IST).strftime("%Y-%m-%d"),
        "Underlying": underlying,
        "ExpiryDate": None,
        "Position": "NONE",
        "OptionSymbol": None,
        "SecurityId": None,
        "Quantity": 0,
        "EntryPrice": 0.0,
        "EntryIndexPrice": 0.0,
        "StopLossPrice": None,
        "TargetPrice": None,
        "LastSignalTime": None,
        "MorningHigh": None,
        "MorningLow": None,
        "AfternoonHigh": None,
        "AfternoonLow": None,
        "TriggerCandleHigh": None,
        "TriggerCandleLow": None,
        "TriggerCandleStamp": None,
        "TradedTriggerStamp": None,
        "TMEntryOperator": None,
        "MaxLossBreachCount": 0,
        "TMLastExitReason": None,
        "TMReEntryUsed": False
    }

def load_and_verify_active_state(dhan, underlying, state_file):
    """
    Checks if there is a saved state for the current session.
    Verifies any active positions with the Dhan broker terminal.
    Returns the state dict or a default clean state if recovery fails/does not exist.
    """
    recovered_state = load_trade_state(state_file, underlying)
    
    if recovered_state:
        safe_print("\n" + "="*50)
        safe_print("            RECOVERED STATE DETECTED")
        safe_print("="*50)
        safe_print(f"  • Position:         {recovered_state.get('Position')}")
        safe_print(f"  • Option Contract:  {recovered_state.get('OptionSymbol')}")
        safe_print(f"  • Entry Price:      {recovered_state.get('EntryPrice')}")
        safe_print(f"  • Entry Index:      {recovered_state.get('EntryIndexPrice')}")
        safe_print(f"  • Stop Loss (Idx):  {recovered_state.get('StopLossPrice')}")
        safe_print(f"  • Target (Idx):     {recovered_state.get('TargetPrice')}")
        
        # Load recovered expiry
        recovered_exp_str = recovered_state.get("ExpiryDate")
        if recovered_exp_str:
            try:
                expiry_date = datetime.strptime(recovered_exp_str, "%Y-%m-%d").date()
                safe_print(f"  • Recovered Expiry: {expiry_date.strftime('%d-%b-%Y')}")
            except Exception:
                pass
        safe_print("="*50 + "\n")
        
        # Verify with broker active positions
        is_active_broker = False
        sec_id = recovered_state.get("SecurityId")
        from utils import is_paper_trading
        
        if is_paper_trading():
            is_active_broker = True
        elif recovered_state.get("Position") != "NONE" and sec_id:
            try:
                positions_resp = dhan.get_positions()
                if positions_resp.get("status") == "success":
                    for p in positions_resp.get("data", []):
                        if str(p.get("securityId")) == str(sec_id) and int(p.get("netQty", 0)) > 0:
                            is_active_broker = True
                            recovered_state["Quantity"] = int(p.get("netQty"))
                            break
            except Exception as e:
                safe_print(f"Error checking active broker positions: {e}")
                
        if recovered_state.get("Position") != "NONE" and not is_active_broker:
            safe_print("[WARNING] Active option position not found in broker terminal. Resetting state.")
            recovered_state = None
            
        if recovered_state:
            # Map old keys to new naming for backward compatibility
            if "TimeCandleHigh" in recovered_state:
                recovered_state["TriggerCandleHigh"] = recovered_state.pop("TimeCandleHigh")
            if "TimeCandleLow" in recovered_state:
                recovered_state["TriggerCandleLow"] = recovered_state.pop("TimeCandleLow")
            if "TimeCandleStamp" in recovered_state:
                recovered_state["TriggerCandleStamp"] = recovered_state.pop("TimeCandleStamp")
                
            # Ensure all standard default fields exist in recovered dict
            default_state = get_default_state(underlying)
            for k, v in default_state.items():
                if k not in recovered_state:
                    recovered_state[k] = v
            return recovered_state

    return get_default_state(underlying)

def reset_state_for_new_signal(state, signal_timestamp, sl_price, target_price, target_expiry, date_str):
    """Resets memory state fields when a new signal is successfully parsed from the scraper."""
    state.update({
        "Date": date_str,
        "ExpiryDate": target_expiry.strftime("%Y-%m-%d") if target_expiry else None,
        "Position": "NONE",
        "OptionSymbol": None,
        "SecurityId": None,
        "Quantity": 0,
        "EntryPrice": 0.0,
        "EntryIndexPrice": 0.0,
        "StopLossPrice": sl_price,
        "TargetPrice": target_price,
        "LastSignalTime": signal_timestamp,
        "MorningHigh": None,
        "MorningLow": None,
        "AfternoonHigh": None,
        "AfternoonLow": None,
        "TriggerCandleHigh": None,
        "TriggerCandleLow": None,
        "TriggerCandleStamp": None,
        "TradedTriggerStamp": None,
        "TMEntryOperator": None,
        "MaxLossBreachCount": 0,
        "TMLastExitReason": None,
        "TMReEntryUsed": False
    })

def check_for_scraper_signals(scrape_file, state, expiry_style):
    """
    Checks the scraper output file. If a new signal timestamp is found,
    resets trading parameters and updates state.
    Returns True if a new signal was detected and loaded.
    """
    import re
    from broker import get_dynamic_expiry_date
    
    if not os.path.exists(scrape_file):
        return False
        
    try:
        with open(scrape_file, "r") as f:
            data = json.load(f)
            if not data:
                return False
                
            last_entry = data[-1]
            signal_timestamp = last_entry.get("Timestamp")
            
            if signal_timestamp != state.get("LastSignalTime"):
                sl_str = last_entry.get("SL", "--")
                sl_price = None
                if sl_str != "--":
                    try:
                        sl_price = float(re.sub(r'[^0-9.]', '', sl_str))
                    except Exception:
                        pass
                
                target_str = last_entry.get("TARGET (1:2)", "--")
                target_price = None
                if target_str != "--":
                    try:
                        target_price = float(re.sub(r'[^0-9.]', '', target_str))
                    except Exception:
                        pass
                        
                underlying = state.get("Underlying", "Nifty Bank")
                target_expiry = get_dynamic_expiry_date(underlying, expiry_style)
                
                # Check if it is a new calendar day signal
                new_date_str = signal_timestamp.split(" ")[0] if signal_timestamp else ""
                old_date_str = state.get("LastSignalTime", "").split(" ")[0] if state.get("LastSignalTime") else ""
                
                if new_date_str != old_date_str:
                    # New calendar day signal: Reset everything!
                    reset_state_for_new_signal(
                        state,
                        signal_timestamp,
                        sl_price,
                        target_price,
                        target_expiry,
                        datetime.now(IST).strftime("%Y-%m-%d")
                    )
                else:
                    # Same calendar day signal update: only update timestamps/targets, do NOT reset active trade/ranges!
                    state["LastSignalTime"] = signal_timestamp
                    if sl_price is not None:
                        state["StopLossPrice"] = sl_price
                    if target_price is not None:
                        state["TargetPrice"] = target_price
                        
                # Always update the scraper signal parameters
                state["Bar2Time"] = last_entry.get("Bar 2 Time")
                state["Bar3Time"] = last_entry.get("Bar 3 Time")
                state["Operator"] = last_entry.get("Operator")
                return True
    except Exception as e:
        safe_print(f"Error checking scrape file: {e}")
        
    return False

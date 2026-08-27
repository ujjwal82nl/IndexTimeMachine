import os
import sys
import time
import re
import json
from datetime import datetime, timedelta
import pandas as pd
from utils import (
    IST, SCRIP_MASTER_FILE, safe_print, get_index_metadata, download_scrip_master_if_needed,
    is_paper_trading, get_totp_token, TRADES_FILE
)
from dhanhq import dhanhq, DhanLogin
from dhanhq.dhan_context import DhanContext

class DhanTokenExpiredException(Exception):
    """Custom exception raised when a Dhan API request fails due to an expired or invalid token."""
    pass

def check_response_for_invalid_token(response):
    """Helper to check a Dhan API response dictionary for token validation errors."""
    if isinstance(response, dict) and response.get("status") == "failure":
        remarks = response.get("remarks", {})
        err_msg = str(remarks.get("error_message", "") or remarks.get("error_description", "") or remarks.get("error_code", ""))
        if "Invalid Token" in err_msg or "token" in err_msg.lower() or "unauthorized" in err_msg.lower() or "invalid_token" in err_msg:
            raise DhanTokenExpiredException("Dhan access token is invalid or expired.")

def get_dhan_security_id(trading_symbol):
    """Retrieves the security ID for the index underlying asset."""
    download_scrip_master_if_needed()
    if not os.path.exists(SCRIP_MASTER_FILE):
        fallback = {
            "Nifty 50": 13,
            "Nifty Bank": 25,
            "S&P BSE Sensex": 1,
            "Fin Nifty": 27
        }
        return fallback.get(trading_symbol, None)
        
    try:
        df = pd.read_csv(SCRIP_MASTER_FILE, low_memory=False)
        meta = get_index_metadata(trading_symbol)
        target_symbol = meta["symbol"]
        
        filtered = df[
            (df['SEM_INSTRUMENT_NAME'].str.upper() == 'INDEX') &
            (df['SEM_TRADING_SYMBOL'].str.upper() == target_symbol)
        ]
        
        if not filtered.empty:
            return int(filtered.iloc[0]['SEM_SMST_SECURITY_ID'])
    except Exception as e:
        safe_print(f"Error looking up index security ID: {e}")
        
    fallback = {
        "Nifty 50": 13,
        "Nifty Bank": 25,
        "S&P BSE Sensex": 1,
        "Fin Nifty": 27
    }
    return fallback.get(trading_symbol, None)

def get_dynamic_expiry_date(underlying_symbol, expiry_style="WEEKLY"):
    """
    Calculates target expiry date to protect against theta decay:
    - Never selects same-week or too close expiry.
    - Skips if same calendar week OR if < 3 calendar days away.
    - If style is "MONTHLY", filters to keep only the last expiry of each month.
    """
    download_scrip_master_if_needed()
    if not os.path.exists(SCRIP_MASTER_FILE):
        return None
        
    try:
        df = pd.read_csv(SCRIP_MASTER_FILE, low_memory=False)
        meta = get_index_metadata(underlying_symbol)
        base_symbol = meta["symbol"]
        
        # Filter for options contracts of base symbol
        df_opts = df[
            df['SEM_TRADING_SYMBOL'].str.startswith(base_symbol) &
            (df['SEM_TRADING_SYMBOL'].str.endswith('CE') | df['SEM_TRADING_SYMBOL'].str.endswith('PE'))
        ].copy()
        
        df_opts['expiry_dt'] = pd.to_datetime(df_opts['SEM_EXPIRY_DATE'], errors='coerce')
        df_opts = df_opts.dropna(subset=['expiry_dt'])
        
        # Get unique future expiries (including today)
        now_date = datetime.now(IST).date()
        future_dates = df_opts[df_opts['expiry_dt'].dt.date >= now_date]['expiry_dt'].dt.date.unique()
        unique_expiries = sorted(list(future_dates))
        
        if not unique_expiries:
            return None
            
        # If monthly style, keep only the last expiry of each calendar month
        if expiry_style.upper() == "MONTHLY":
            monthly_expiries = {}
            for exp in unique_expiries:
                key = (exp.year, exp.month)
                if key not in monthly_expiries or exp > monthly_expiries[key]:
                    monthly_expiries[key] = exp
            unique_expiries = sorted(list(monthly_expiries.values()))
            
        # Find target expiry applying the too-close rules
        for exp in unique_expiries:
            is_same_week = (exp.isocalendar()[1] == now_date.isocalendar()[1]) and (exp.year == now_date.year)
            is_less_than_3_days = (exp - now_date).days < 3
            
            if is_same_week or is_less_than_3_days:
                continue
            else:
                return exp
                
    except Exception as e:
        safe_print(f"Error calculating dynamic expiry: {e}")
        
    return None

def find_option_security_id(underlying_symbol, target_expiry, strike, option_type):
    """
    Searches the instrument master for the specific CE or PE option contract.
    Ensures exact lookup based on strike, expiry date, and option type.
    """
    download_scrip_master_if_needed()
    if not os.path.exists(SCRIP_MASTER_FILE) or target_expiry is None:
        return None, None
        
    try:
        df = pd.read_csv(SCRIP_MASTER_FILE, low_memory=False)
        meta = get_index_metadata(underlying_symbol)
        base_symbol = meta["symbol"]
        
        f1 = df[
            (df['SEM_TRADING_SYMBOL'].str.startswith(base_symbol)) &
            (df['SEM_TRADING_SYMBOL'].str.endswith(option_type))
        ]
        
        f2 = f1[f1['SEM_TRADING_SYMBOL'].str.contains(str(strike))]
        
        f2['expiry_dt'] = pd.to_datetime(f2['SEM_EXPIRY_DATE'], errors='coerce')
        f3 = f2[f2['expiry_dt'].dt.date == target_expiry]
            
        if not f3.empty:
            sec_id = int(f3.iloc[0]['SEM_SMST_SECURITY_ID'])
            trad_sym = f3.iloc[0]['SEM_TRADING_SYMBOL']
            return sec_id, trad_sym
            
    except Exception as e:
        safe_print(f"Error looking up option contract: {e}")
        
    return None, None

def get_current_price(dhan, security_id):
    """Fetches real-time LTP of the index using SDK ohlc_data with a fallback."""
    try:
        response = dhan.ohlc_data(securities={"IDX_I": [int(security_id)]})
        if response.get("status") == "success":
            data = response.get("data", {})
            if "data" in data:
                data = data["data"]
            seg_data = data.get("IDX_I", {})
            inst_data = seg_data.get(str(security_id), {})
            ltp = inst_data.get("lastPrice") or inst_data.get("last_price") or inst_data.get("close")
            if ltp is not None:
                return float(ltp)
    except Exception as e:
        safe_print(f"LTP fetch failed: {e}")
        
    # Fallback to last minute close
    try:
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        history_df = get_intraday_candles(dhan, security_id, "IDX_I", "INDEX", today_str, today_str)
        if history_df is not None and not history_df.empty:
            return float(history_df.iloc[-1]['close'])
    except Exception:
        pass
        
    return None

def get_option_price(dhan, opt_sec_id, underlying="Nifty 50"):
    """Fetches option contract premium price via ohlc_data with fallback to intraday candle close."""
    segment = "BSE_FNO" if "Sensex" in underlying or "BSE" in underlying else "NSE_FNO"
    try:
        response = dhan.ohlc_data(securities={segment: [int(opt_sec_id)]})
        check_response_for_invalid_token(response)
        if response.get("status") == "success":
            data = response.get("data", {})
            if "data" in data:
                data = data["data"]
            fno_data = data.get(segment, {})
            inst_data = fno_data.get(str(opt_sec_id), {})
            ltp = inst_data.get("lastPrice") or inst_data.get("last_price") or inst_data.get("close")
            if ltp is not None:
                return float(ltp)
    except DhanTokenExpiredException:
        raise
    except Exception as e:
        err_str = str(e).lower()
        if "401" in err_str or "unauthorized" in err_str or "invalid token" in err_str or "invalid_token" in err_str:
            raise DhanTokenExpiredException(f"Dhan access token is invalid: {e}")
        safe_print(f"Option premium fetch failed: {e}")
        
    # Fallback to last minute close
    try:
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        history_df = get_intraday_candles(dhan, opt_sec_id, segment, "OPTIDX", today_str, today_str)
        if history_df is not None and not history_df.empty:
            return float(history_df.iloc[-1]['close'])
    except Exception:
        pass
        
    return None

_candles_cache = {}

def get_intraday_candles(dhan, security_id, segment, instrument, from_date, to_date):
    """Fetches intraday historical candles using SDK intraday_minute_data with 45s caching."""
    global _candles_cache
    cache_key = (str(security_id), segment, instrument, from_date, to_date)
    now = time.time()
    
    # Return cached data if request is within 45 seconds to prevent rate limit breaches
    if cache_key in _candles_cache:
        cached_time, cached_df = _candles_cache[cache_key]
        if now - cached_time < 45:
            return cached_df.copy()
            
    try:
        history = dhan.intraday_minute_data(
            security_id=str(security_id),
            exchange_segment=segment,
            instrument_type=instrument,
            from_date=from_date,
            to_date=to_date
        )
        check_response_for_invalid_token(history)
        if history.get("status") == "success" and "data" in history:
            data = history["data"]
            timestamps = pd.to_datetime(data['timestamp'], unit='s').tz_localize('UTC').tz_convert('Asia/Kolkata')
            df = pd.DataFrame({
                'time': timestamps,
                'open': data['open'],
                'high': data['high'],
                'low': data['low'],
                'close': data['close']
            })
            # Save to cache
            _candles_cache[cache_key] = (now, df)
            return df.copy()
        else:
            remarks = history.get("remarks", {})
            err_msg = remarks.get("error_message") or remarks.get("error_description") or "Unknown API Error"
            safe_print(f"[WARNING] Dhan historical candle query failed: {err_msg}")
    except DhanTokenExpiredException:
        raise
    except Exception as e:
        err_str = str(e).lower()
        if "401" in err_str or "unauthorized" in err_str or "invalid token" in err_str or "invalid_token" in err_str:
            raise DhanTokenExpiredException(f"Dhan access token is invalid: {e}")
        safe_print(f"Error calling SDK intraday_minute_data: {e}")
        
    return None

def get_5min_candle_range(dhan, security_id, candle_time_str):
    """Fetches high/low of the specific 5-min candle from Dhan 1-minute historical data."""
    match = re.search(r'\b(\d{2}):(\d{2})\b', candle_time_str)
    if not match:
        return None, None
        
    target_hour = int(match.group(1))
    target_minute = int(match.group(2))
    
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    date_match = re.search(r'\b(\d{1,2})\s+([A-Za-z]{3})', candle_time_str)
    now = datetime.now(IST)
    if date_match:
        day = int(date_match.group(1))
        month_str = date_match.group(2).capitalize()
        month = months.index(month_str) + 1
        query_date = datetime(now.year, month, day).strftime("%Y-%m-%d")
    else:
        query_date = now.strftime("%Y-%m-%d")
        
    df = get_intraday_candles(dhan, security_id, "IDX_I", "INDEX", query_date, query_date)
    if df is not None and not df.empty:
        try:
            start_t = datetime.strptime(f"{target_hour:02d}:{target_minute:02d}", "%H:%M").time()
            end_min = target_minute + 5
            end_hour = target_hour
            if end_min >= 60:
                end_min -= 60
                end_hour += 1
            end_t = datetime.strptime(f"{end_hour:02d}:{end_min:02d}", "%H:%M").time()
            
            df_filtered = df[
                (df['time'].dt.time >= start_t) &
                (df['time'].dt.time < end_t)
            ]
            
            if not df_filtered.empty:
                return float(df_filtered['high'].max()), float(df_filtered['low'].min())
        except Exception as e:
            safe_print(f"Error resampling 5m candle: {e}")
            
    return None, None

def check_and_get_time_candle(dhan, security_id, range_high, range_low, operator, time_candle_str):
    """
    Evaluates 5-minute candles chronologically starting after time_candle_str.
    Tracks active Trigger Candle and Cancellation conditions.
    Returns (breakout_value, timestamp, api_success).
    """
    if range_high is None or range_low is None or not time_candle_str:
        return None, None, False
        
    match = re.search(r'\b(\d{2}):(\d{2})\b', time_candle_str)
    if not match:
        return None, None, False
        
    target_hour = int(match.group(1))
    target_min = int(match.group(2))
    time_candle_time = datetime.strptime(f"{target_hour:02d}:{target_min:02d}", "%H:%M").time()
    
    now = datetime.now(IST)
    query_date = now.strftime("%Y-%m-%d")
    
    df = get_intraday_candles(dhan, security_id, "IDX_I", "INDEX", query_date, query_date)
    if df is not None and not df.empty:
        try:
            df = df.copy()
            df.set_index('time', inplace=True)
            df_5m = df.resample('5Min', closed='left', label='left').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last'
            }).dropna()
            
            active_trigger_val = None
            active_trigger_stamp = None
            
            for timestamp, row in df_5m.iterrows():
                # Only check candles starting strictly after the Time Candle time
                if timestamp.time() > time_candle_time:
                    close_price = float(row['close'])
                    if operator == "Buy on Dip":
                        if close_price > range_high:
                            if active_trigger_val is None:
                                active_trigger_val = float(row['high'])
                                active_trigger_stamp = timestamp.strftime("%H:%M")
                        elif close_price < range_low:
                            active_trigger_val = None
                            active_trigger_stamp = None
                    else: # Sell on Rise
                        if close_price < range_low:
                            if active_trigger_val is None:
                                active_trigger_val = float(row['low'])
                                active_trigger_stamp = timestamp.strftime("%H:%M")
                        elif close_price > range_high:
                            active_trigger_val = None
                            active_trigger_stamp = None
                            
            return active_trigger_val, active_trigger_stamp, True
        except Exception as e:
            safe_print(f"Error evaluating 5m candles: {e}")
            return None, None, False
            
    return None, None, False

def get_atm_itm_strike(index_name, price, option_type):
    """
    Calculates strike price ensuring it is ATM or ITM (Never OTM).
    For CALL (CE): Round down index price (strike <= index price).
    For PUT (PE): Round up index price (strike >= index price).
    """
    meta = get_index_metadata(index_name)
    step = meta["strikeStep"]
    price_int = int(price)
    if option_type == "CE":
        return (price_int // step) * step
    else:
        return ((price_int + step - 1) // step) * step

def execute_dhan_order(dhan, underlying, target_expiry, strike, option_type, action, index_price, client_code, quantity, strategy_tag="TIME_MACHINE", extra_details=None):
    """Sends F&O order using SDK place_order and logs execution result.
    
    Args:
        strategy_tag: Tag written to trades.json for strategy comparison.
                      Use 'TIME_MACHINE' (default) or 'INDICATOR'.
    """
    sec_id, trad_sym = find_option_security_id(underlying, target_expiry, strike, option_type)
    if not sec_id:
        safe_print(f"\n[ERROR] Option contract not found in master list for {underlying} Strike {strike} {option_type} Expiry {target_expiry}")
        return False, None, None
        
    paper_mode = is_paper_trading()
    if paper_mode:
        safe_print("\n" + "*"*50)
        safe_print(f"     [PAPER TRADE] ORDER SIMULATED ({action})")
        safe_print("*"*50)
        safe_print(f"  • Timestamp:      {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}")
        safe_print(f"  • Instrument:     {trad_sym} (ID: {sec_id})")
        safe_print(f"  • Strike:         {strike} {option_type} (ATM/ITM)")
        safe_print(f"  • Action:         {action}")
        safe_print(f"  • Quantity:       {quantity}")
        safe_print(f"  • Index Price:    {index_price}")
        safe_print("*"*50 + "\n")
        
        response = {
            "status": "success",
            "remarks": "Simulated Paper Trade order fill",
            "data": {
                "orderId": f"PAPER_{int(datetime.now(IST).timestamp())}"
            }
        }
    else:
        safe_print("\n" + "*"*50)
        safe_print(f"           ORDER SENT TO DHAN ({action})")
        safe_print("*"*50)
        safe_print(f"  • Timestamp:      {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}")
        safe_print(f"  • Instrument:     {trad_sym} (ID: {sec_id})")
        safe_print(f"  • Strike:         {strike} {option_type} (ATM/ITM)")
        safe_print(f"  • Action:         {action}")
        safe_print(f"  • Quantity:       {quantity}")
        safe_print(f"  • Index Price:    {index_price}")
        safe_print("*"*50 + "\n")
        
        try:
            segment = "BSE_FNO" if "Sensex" in underlying or "BSE" in underlying else "NSE_FNO"
            response = dhan.place_order(
                security_id=str(sec_id),
                exchange_segment=segment,
                transaction_type=action,
                quantity=int(quantity),
                order_type="MARKET",
                product_type="INTRADAY"
            )
            check_response_for_invalid_token(response)
            
            if isinstance(response, dict) and response.get("status") == "failure":
                remarks = response.get("remarks", {})
                if isinstance(remarks, dict):
                    err_msg = remarks.get("error_message") or remarks.get("error_description") or remarks.get("error_code")
                else:
                    err_msg = str(remarks)
                safe_print(f"[ERROR] Dhan order rejected: {err_msg}")
                return False, None, None
        except DhanTokenExpiredException:
            raise
        except Exception as e:
            err_str = str(e).lower()
            if "401" in err_str or "unauthorized" in err_str or "invalid token" in err_str or "invalid_token" in err_str:
                raise DhanTokenExpiredException(f"Dhan access token is invalid: {e}")
            safe_print(f"[ERROR] Live order execution failed: {e}")
            return False, None, None
        
    try:
        return True, trad_sym, sec_id
    except Exception as e:
        return False, None, None

def is_token_valid(dhan, security_id=13):
    try:
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        response = dhan.intraday_minute_data(
            security_id=str(security_id),
            exchange_segment="IDX_I",
            instrument_type="INDEX",
            from_date=today_str,
            to_date=today_str
        )
        if isinstance(response, dict) and response.get("status") == "failure":
            remarks = response.get("remarks", {})
            err_msg = str(remarks.get("error_message", "") or remarks.get("error_description", ""))
            if "Invalid Token" in err_msg or "token" in err_msg.lower() or "unauthorized" in err_msg.lower():
                return False
        return True
    except Exception as e:
        err_str = str(e).lower()
        if "401" in err_str or "unauthorized" in err_str or "invalid token" in err_str or "invalid_token" in err_str or "token is invalid" in err_str:
            safe_print(f"[DEBUG] Dhan token verified as INVALID: {e}")
            return False
        safe_print(f"[DEBUG] Dhan token check failed due to temporary network/rate issue (assuming token remains valid): {e}")
        return True

def acquire_token_lock():
    lock_file = ".dhan_token.lock"
    # Try to acquire the lock. If it fails, sleep 1 second and retry, up to 180 seconds.
    start_t = time.time()
    while time.time() - start_t < 180:
        try:
            fp = open(lock_file, "w")
            if os.name == 'nt':
                import msvcrt
                fp.seek(0)
                msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fp
        except (ImportError, IOError, OSError):
            try:
                fp.close()
            except Exception:
                pass
            time.sleep(1)
    safe_print("[WARNING] Token lock acquisition timed out after 180 seconds.")
    return None

def release_token_lock(fp):
    if fp:
        try:
            if os.name == 'nt':
                import msvcrt
                fp.seek(0)
                msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
            fp.close()
        except Exception:
            try:
                fp.close()
            except Exception:
                pass

def authenticate_and_get_dhan_client(client_code, totp_secret, pin, force_refresh=False, current_token=None):
    token_file = ".dhan_token.json"
    access_token = None
    
    lock_fp = acquire_token_lock()
    try:
        # If we are forcing a refresh, check if another instance has already written a new token
        if force_refresh and os.path.exists(token_file):
            try:
                with open(token_file, "r") as f:
                    token_data = json.load(f)
                cached_token = token_data.get("accessToken")
                if cached_token and cached_token != current_token:
                    # Test if the newly found cached token is valid
                    test_dhan = dhanhq(DhanContext(client_code, cached_token))
                    if is_token_valid(test_dhan):
                        safe_print("Detected a new valid cached token generated by another instance. Bypassing TOTP generation.")
                        return test_dhan, cached_token
            except Exception:
                pass
                
        if not force_refresh and os.path.exists(token_file):
            try:
                with open(token_file, "r") as f:
                    token_data = json.load(f)
                expiry_str = token_data.get("expiryTime")
                if expiry_str:
                    expiry_dt = datetime.fromisoformat(expiry_str)
                    now_ist_naive = datetime.now(IST).replace(tzinfo=None)
                    if expiry_dt > now_ist_naive + timedelta(minutes=5):
                        temp_token = token_data.get("accessToken")
                        test_dhan = dhanhq(DhanContext(client_code, temp_token))
                        if is_token_valid(test_dhan):
                            access_token = temp_token
                            safe_print(f"Loaded valid cached Dhan access token. (Expires: {expiry_str})")
                        else:
                            safe_print("[WARNING] Cached Dhan access token is invalid on server (e.g. new calendar day). Re-authenticating...")
            except Exception as e:
                safe_print(f"[WARNING] Failed to load/parse cached token: {e}")
    
        if not access_token:
            # Get dynamic TOTP code and authorize with Dhan SDK with retry logic
            for attempt in range(3):
                totp_code = get_totp_token(totp_secret)
                login_client = DhanLogin(client_code)
                try:
                    safe_print(f"Requesting Dhan access token (Attempt {attempt+1}/3, TOTP code: {totp_code})...")
                    auth_response = login_client.generate_token(pin, totp_code)
                    access_token = auth_response.get("accessToken")
                    if access_token:
                        # Save token cache
                        try:
                            with open(token_file, "w") as f:
                                json.dump(auth_response, f, indent=2)
                            safe_print(f"Saved generated Dhan access token to {token_file}")
                        except Exception as e:
                            safe_print(f"[WARNING] Failed to save token cache: {e}")
                        break
                    else:
                        safe_print(f"[WARNING] Dhan authentication response details: {auth_response}")
                        if "once every 2 minutes" in str(auth_response.get("message", "")).lower():
                            if attempt < 2:
                                safe_print("Rate limit hit. Sleeping 125 seconds to wait out the 2-minute limit...")
                                time.sleep(125)
                                continue
                except Exception as e:
                    safe_print(f"[WARNING] Authentication attempt {attempt+1} failed: {e}")
                
                if attempt < 2:
                    # Calculate sleep time to roll over to a new 30-second TOTP window
                    time_elapsed = int(time.time()) % 30
                    sleep_time = (30 - time_elapsed) + 1
                    # Ensure a minimum sleep of 10 seconds to avoid spamming the API too rapidly
                    if sleep_time < 10:
                        sleep_time += 30
                    safe_print(f"Sleeping {sleep_time} seconds to roll over to a new TOTP time-step...")
                    time.sleep(sleep_time)
                    
        if not access_token:
            safe_print("[ERROR] Access token could not be fetched after 3 attempts. Check your credentials.")
            sys.exit(1)
            
        context = DhanContext(client_code, access_token)
        return dhanhq(context), access_token
    finally:
        release_token_lock(lock_fp)

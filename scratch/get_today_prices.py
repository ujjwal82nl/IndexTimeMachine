import json
import sys
import os
import pandas as pd

# Append parent directory to sys.path to find utils and broker
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from utils import ENV_FILE
from broker import authenticate_and_get_dhan_client, get_intraday_candles

def print_option_candles(dhan, sec_id, name, date_str="2026-08-06"):
    print(f"\n=== {name} (ID: {sec_id}) ===")
    try:
        df = get_intraday_candles(dhan, str(sec_id), "NSE_FNO", "OPTIDX", date_str, date_str)
        if df is not None and not df.empty:
            df.set_index('time', inplace=True)
            df = df.sort_index()
            # print select times
            times_to_check = ["12:35", "12:54", "12:55", "12:56", "13:01", "13:05", "13:06", "13:10", "13:11", "13:16", "13:25"]
            for t_str in times_to_check:
                match_ts = pd.to_datetime(f"{date_str} {t_str}:00")
                # find closest or exact
                for ts in df.index:
                    if ts.strftime("%H:%M") == t_str:
                        row = df.loc[ts]
                        print(f"  [{t_str}] Open: {row['open']:.2f} | High: {row['high']:.2f} | Low: {row['low']:.2f} | Close: {row['close']:.2f}")
                        break
        else:
            print("  No candles retrieved.")
    except Exception as e:
        print(f"  Error: {e}")

def main():
    if not os.path.exists(ENV_FILE):
        print(f"Error: Credentials file {ENV_FILE} not found!")
        return

    with open(ENV_FILE, "r") as f:
        config = json.load(f)
        dhan_config = config.get("dhan_config", {})
        client_code = dhan_config.get("client_code")
        totp_secret = dhan_config.get("totp_secret")
        pin = dhan_config.get("pin")

    dhan, _ = authenticate_and_get_dhan_client(client_code, totp_secret, pin)

    # 1. Nifty 50 PE Option: 41019
    print_option_candles(dhan, 41019, "Nifty 50 PE 24650")

    # 2. Nifty Bank CE Option: 59096
    print_option_candles(dhan, 59096, "Nifty Bank CE 57800")

    # 3. Nifty Midcap Select PE Option: 60632
    print_option_candles(dhan, 60632, "Nifty Midcap Select PE 14925")

if __name__ == "__main__":
    main()

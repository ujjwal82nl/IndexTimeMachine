import json
import sys
import os

# Append parent directory to sys.path to find utils and broker
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from utils import ENV_FILE
from broker import authenticate_and_get_dhan_client

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

    print("Authenticating with Dhan API...")
    dhan, _ = authenticate_and_get_dhan_client(client_code, totp_secret, pin)

    print("\n--- Test 1: Fetching Nifty 50 Index (ID 13) Intraday Minute Data for 2026-08-05 ---")
    try:
        response = dhan.intraday_minute_data(
            security_id="13",
            exchange_segment="IDX_I",
            instrument_type="INDEX",
            from_date="2026-08-05",
            to_date="2026-08-05"
        )
        print("Raw Response Keys:", response.keys() if isinstance(response, dict) else type(response))
        print("Raw Response:", json.dumps(response, indent=2) if isinstance(response, dict) else response)
    except Exception as e:
        print("Exception during Test 1:", e)

if __name__ == "__main__":
    main()

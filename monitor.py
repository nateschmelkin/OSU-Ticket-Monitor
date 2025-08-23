import os
import time
import csv
import sys
import pytz
import math
import signal
import logging
from datetime import datetime
from typing import Dict, Any, Optional

# --- Dependency check: help user if venv not activated ---
required_modules = ["yaml", "requests", "bs4", "pytz"]
missing = []
for mod in required_modules:
    try:
        __import__(mod)
    except ImportError:
        missing.append(mod)

if missing:
    print("\n🚨 Missing dependencies:", ", ".join(missing))
    print("👉 Make sure your virtual environment is active:")
    print("   cd ~/Projects/maizetix_monitor")
    print("   source .venv/bin/activate")
    print("Then install dependencies with:")
    print("   pip install -r requirements.txt\n")
    sys.exit(1)
# ---------------------------------------------------------

import yaml
from parse_event import fetch_event_page, parse_prices_summary
from notifier import Notifier

LOG_FMT = "[%(asctime)s] %(levelname)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
HISTORY_CSV = os.path.join(DATA_DIR, "price_history.csv")


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)


def read_last_lowest() -> Optional[float]:
    if not os.path.exists(HISTORY_CSV):
        return None
    try:
        with open(HISTORY_CSV, "r", newline="") as f:
            reader = csv.DictReader(f)
            lows = [
                float(row["lowest_price"]) for row in reader if row.get("lowest_price")
            ]
            return min(lows) if lows else None
    except Exception:
        return None


def read_last_state() -> Dict[str, Any]:
    """Read the last recorded state to detect changes"""
    if not os.path.exists(HISTORY_CSV):
        return {}
    try:
        with open(HISTORY_CSV, "r", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            if not rows:
                return {}
            last_row = rows[-1]

            # Parse all_prices safely
            all_prices_str = last_row.get("all_prices", "[]")
            all_prices = []
            if all_prices_str and all_prices_str != "[]":
                try:
                    # Remove brackets and split by comma, then convert to float
                    prices_str = all_prices_str.strip("[]")
                    if prices_str:
                        all_prices = [float(p.strip()) for p in prices_str.split(",")]
                except (ValueError, AttributeError):
                    all_prices = []

            return {
                "lowest_price": (
                    float(last_row["lowest_price"])
                    if last_row.get("lowest_price")
                    else None
                ),
                "num_listings": (
                    int(last_row["num_listings"])
                    if last_row.get("num_listings")
                    else None
                ),
                "all_prices": all_prices,
            }
    except Exception:
        return {}


def append_history(row: Dict[str, Any]):
    ensure_dirs()
    file_exists = os.path.exists(HISTORY_CSV)
    with open(HISTORY_CSV, "a", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp",
                "lowest_price",
                "page_lowest_price",
                "computed_min_price",
                "median_sale",
                "num_listings",
                "event_url",
                "all_prices",
            ],
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def format_usd(v: Optional[float]) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "N/A"
    return f"${v:,.2f}"


def main():
    cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    if not os.path.exists(cfg_path):
        logging.error(
            "Missing config.yaml. Copy config.example.yaml and edit it first."
        )
        sys.exit(1)

    cfg = load_config(cfg_path)
    event_url = cfg["event_url"]
    period = int(cfg.get("check_every_seconds", 300))
    target_price = float(cfg.get("target_price", 0))
    notify_on_new_low = bool(cfg.get("notify_on_new_low", True))
    tz_name = cfg.get("timezone", "America/Detroit")
    user_agent = cfg.get("user_agent", "MaizeTixMonitor/1.0 (+personal use)")

    # NEW: allow single-pass mode via env var RUN_ONCE=1 or config run_once: true
    run_once = bool(int(os.getenv("RUN_ONCE", "0"))) or bool(cfg.get("run_once", False))

    tz = pytz.timezone(tz_name)
    notifier = Notifier()  # Slack via env SLACK_WEBHOOK_URL

    logging.info(
        f"Monitoring {event_url} every {period}s | target={format_usd(target_price)} | run_once={run_once}"
    )

    # Graceful stop via Ctrl+C
    stop = False

    def handle_sigint(sig, frame):
        nonlocal stop
        stop = True
        logging.info("Stopping...")

    signal.signal(signal.SIGINT, handle_sigint)

    all_time_low = read_last_lowest()
    last_state = read_last_state()  # track previous state for movement detection

    while not stop:
        try:
            html = fetch_event_page(event_url, user_agent=user_agent)
            summary = parse_prices_summary(html)

            now = datetime.now(tz)
            ts_str = now.strftime("%Y-%m-%d %I:%M %p")  # timestamp for alerts

            row = {
                "timestamp": now.isoformat(),
                "lowest_price": summary.get("lowest_price"),
                "page_lowest_price": summary.get("lowest_price"),
                "computed_min_price": summary.get("computed_min_price"),
                "median_sale": summary.get("median_sale"),
                "num_listings": summary.get("num_listings"),
                "event_url": event_url,
                "all_prices": str(summary.get("all_prices", [])),
            }
            append_history(row)

            lp = summary.get("lowest_price")
            cmp_min = summary.get("computed_min_price")
            num_listings = summary.get("num_listings")
            all_prices = summary.get("all_prices", [])

            msg_bits = [
                f"lowest={format_usd(lp)}",
                f"computed_min={format_usd(cmp_min)}",
                f"median_sale={format_usd(summary.get('median_sale'))}",
                f"listings={num_listings}",
            ]
            logging.info(" | ".join(msg_bits))

            trigger_msgs = []
            movement_detected = False

            # 🎯 Price target alert
            if lp is not None and target_price > 0 and lp <= target_price:
                trigger_msgs.append(
                    f"✅ [{ts_str}] Target hit: lowest={format_usd(lp)} ≤ {format_usd(target_price)}"
                )

            # 📉 New all-time low alert
            if notify_on_new_low:
                effective_low = min(
                    [v for v in [lp, cmp_min] if v is not None], default=None
                )
                if effective_low is not None:
                    if all_time_low is None or effective_low < all_time_low - 1e-9:
                        trigger_msgs.append(
                            f"📉 [{ts_str}] New all-time low: {format_usd(effective_low)} (old={format_usd(all_time_low)})"
                        )
                        all_time_low = effective_low

            # 🆕 Ticket movement detection (posted or sold)
            if last_state and num_listings is not None:
                last_num = last_state.get("num_listings")
                last_lowest = last_state.get("lowest_price")

                if last_num is not None and num_listings != last_num:
                    diff = num_listings - last_num
                    if diff > 0:
                        trigger_msgs.append(
                            f"🆕 [{ts_str}] {diff} new ticket(s) posted! Total listings: {num_listings}"
                        )
                    else:
                        trigger_msgs.append(
                            f"💰 [{ts_str}] {abs(diff)} ticket(s) sold! Total listings: {num_listings}"
                        )
                    movement_detected = True

                # Price change detection
                if (
                    last_lowest is not None
                    and lp is not None
                    and abs(lp - last_lowest) > 0.01
                ):
                    change = "↗️" if lp > last_lowest else "↘️"
                    trigger_msgs.append(
                        f"{change} [{ts_str}] Price change: {format_usd(last_lowest)} → {format_usd(lp)}"
                    )
                    movement_detected = True

            # Always include current market info in movement notifications
            if movement_detected and all_prices:
                # Lowest 5 tickets
                lowest_5 = all_prices[:5] if len(all_prices) >= 5 else all_prices
                lowest_5_str = ", ".join([format_usd(p) for p in lowest_5])

                # Median of bottom 10
                bottom_10 = all_prices[:10] if len(all_prices) >= 10 else all_prices
                if bottom_10:
                    median_bottom_10 = sorted(bottom_10)[len(bottom_10) // 2]
                    trigger_msgs.append(
                        f"📊 Market update: Lowest 5: [{lowest_5_str}] | "
                        f"Median (bottom 10): {format_usd(median_bottom_10)}"
                    )

            # Update last state
            last_state = {
                "lowest_price": lp,
                "num_listings": num_listings,
                "all_prices": all_prices,
            }

            # 🚨 Send any alerts
            if trigger_msgs:
                notifier.notify(
                    "\n".join(trigger_msgs),
                    event_url=event_url,
                    lowest_price=lp,
                    median_sale=summary.get("median_sale"),
                    num_listings=num_listings,
                )

        except Exception as e:
            logging.exception(f"Error during check: {e}")

        # In CI single-pass mode, do one iteration and exit
        if run_once:
            break

        # Sleep loop
        for _ in range(period):
            if stop:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()

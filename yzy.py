import threading
import curses
import datetime
import time
import eth_account
import requests
import sys
import os
from colorama import Fore, init
init(autoreset=True)

from hyperliquid.utils import constants
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

# ============ 基礎配置 ============
ACCOUNT_ADDRESS = ""
PRIVATE_KEY = ""
TG_TOKEN = ""
TG_CHAT_ID = ""
COIN = "YZY"

# ============ 參數 ============
UPDATE_THRESHOLD = 0.0035
GRID_LEVELS = 33
GRID_RANGE_PCT = 0.035
GRID_QUANTITY = 50
UPDATE_INTERVAL = 15
MAX_POSITION_SIZE = 1800
REPORT_INTERVAL = 1800
last_report_time = 0
last_center_price = 0.0

# ============ 全域狀態 ============
status_data = {
    "position": 0.0, "pnl": 0.0, "pnl_pct": 0.0,
    "price": 0.0, "account_value": 0.0, "entry_px": 0.0
}
status_lock = threading.Lock()
log_lines = []
log_max_lines = 50
running = True

# ============ TG 通知 ============
def send_tg_msg(msg):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": msg}, timeout=30)
    except Exception as e:
        print(f"TG發送失敗: {e}")

# ============ PNL 檔案管理 ============
def record_daily_pnl(current_pnl):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    filename = "YZY_pnl_history.txt"
    if os.path.exists(filename):
        with open(filename, "r") as f:
            lines = f.readlines()
            if lines and lines[-1].startswith(today):
                return
    with open(filename, "a") as f:
        f.write(f"{today},{current_pnl:.2f}\n")
    add_log(f"損益已存檔: {today} | {current_pnl:.2f} USD")

def get_7day_total_pnl():
    filename = "YZY_pnl_history.txt"
    if not os.path.exists(filename): return 0.0
    try:
        with open(filename, "r") as f:
            lines = [l.strip() for l in f.readlines() if "," in l]
            last_7 = [float(l.split(",")[1]) for l in lines[-7:]]
            return sum(last_7)
    except Exception as e:
        add_log(f"讀取PNL失敗: {e}")
        return 0.0

# ============ 更新狀態 ============
def update_status(info, coin):
    try:
        all_mids = info.all_mids()
        if coin not in all_mids or all_mids[coin] is None:
            add_log("無法從 all_mids 獲取價格")
            return False
        price = float(all_mids[coin])

        user_state = info.user_state(ACCOUNT_ADDRESS)
        margin_summary = user_state.get('marginSummary', {})
        account_value = float(margin_summary.get('accountValue', 0.0))
        unrealized_pnl = float(margin_summary.get('unrealizedPnl', 0.0))

        pos_size = entry_px = position_pnl = 0.0
        asset_positions = user_state.get('assetPositions', [])
        for pos in asset_positions:
            position = pos.get('position', {})
            if position.get('coin') == coin:
                pos_size = float(position.get('szi', '0'))
                entry_px = float(position.get('entryPx', '0'))
                position_pnl = float(position.get('unrealizedPnl', '0'))
                break

        if unrealized_pnl == 0.0 and position_pnl != 0.0:
            unrealized_pnl = position_pnl

        if pos_size != 0 and entry_px != 0:
            base_cost = abs(pos_size) * entry_px
            pnl_pct = (unrealized_pnl / base_cost) * 100 if base_cost > 0 else 0.0
        else:
            pnl_pct = 0.0

        with status_lock:
            status_data.update({
                "position": pos_size,
                "pnl": unrealized_pnl,
                "pnl_pct": pnl_pct,
                "price": price,
                "account_value": account_value,
                "entry_px": entry_px
            })
        return True

    except Exception as e:
        add_log(f"狀態更新失敗: {type(e).__name__}: {e}")
        return False

# ============ 日誌系統 ============
def add_log(msg):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    log_msg = f"[{timestamp}] {msg}"
    log_lines.append(log_msg)
    if len(log_lines) > log_max_lines:
        log_lines.pop(0)
    print(log_msg)

# ============ 繪製畫面 ============
def draw_screen(stdscr):
    global running
    curses.curs_set(0)
    curses.start_color()
    curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
    curses.init_pair(5, curses.COLOR_YELLOW, curses.COLOR_BLACK)

    while running:
        h, w = stdscr.getmaxyx()
        stdscr.erase()

        time_str = datetime.datetime.now().strftime("%A-%B-%p")
        title = f" YZY網格機器人 [{time_str}] "
        stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
        stdscr.addstr(0, 0, title.center(w))
        stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)

        with status_lock:
            data = status_data.copy()

        pnl_color = 1 if data["pnl"] >= 0 else 2
        pos_color = 3 if data["position"] > 0 else (2 if data["position"] < 0 else 1)

        line1 = f"PnL: {data['pnl']:+.2f} USD ({data['pnl_pct']:+.2f}%)".ljust(30)
        line1 += f"POS: {data['position']} YZY".ljust(25)
        line1 += f"PRICE: {data['price']:.5f}".ljust(20)
        line1 += f"Account Value: {data['account_value']:.2f} USDC"
        stdscr.addstr(1, 2, line1)
        stdscr.attron(curses.color_pair(pnl_color) | curses.A_BOLD)
        stdscr.addstr(1, 7, f"{data['pnl']:+.2f} USD ({data['pnl_pct']:+.2f}%)")
        stdscr.attroff(curses.color_pair(pnl_color) | curses.A_BOLD)
        stdscr.attron(curses.color_pair(pos_color))
        stdscr.addstr(1, 37, f"{data['position']} YZY")
        stdscr.attroff(curses.color_pair(pos_color))

        stdscr.hline(2, 0, curses.ACS_HLINE, w)
        stdscr.addstr(2, 0, f"持倉上限: ±{MAX_POSITION_SIZE} YZY", curses.color_pair(5) | curses.A_BOLD)

        stdscr.addstr(3, 0, "RECORD:".ljust(w))
        stdscr.hline(4, 0, curses.ACS_HLINE, w)

        start_line = max(0, len(log_lines) - (h - 6))
        for i, log in enumerate(log_lines[start_line:]):
            if 5 + i < h:
                stdscr.addstr(5 + i, 0, log[:w-1])

        stdscr.refresh()
        time.sleep(0.5)

# ============ 取消訂單 ============
def cancel_all_orders(exchange, info, coin):
    """取消特定幣種的所有訂單，返回取消數量"""
    try:
        orders = info.open_orders(ACCOUNT_ADDRESS)
        cancel_count = 0
        for o in orders:
            if o.get('coin') == coin:
                oid = int(o['oid'])
                exchange.cancel(coin, oid)
                add_log(f"取消訂單 {oid}")
                cancel_count += 1
                time.sleep(0.05)  # 避免請求過快
        return cancel_count
    except Exception as e:
        add_log(f"取消訂單失敗: {e}")
        return 0

# ============ close ============
def close_position(exchange, info, coin):
    """平倉並返回是否成功"""
    try:
        # 先取消所有訂單
        cancel_all_orders(exchange, info, coin)
        time.sleep(0.5)
        
        # 平倉
        response = exchange.market_close(coin)
        
        # 更新狀態確認平倉成功
        time.sleep(1)
        update_status(info, coin)
        
        with status_lock:
            new_pos = status_data["position"]
        
        if abs(new_pos) < 0.001:  # 接近0視為成功
            add_log("✅ 平倉成功")
            return True
        else:
            add_log(f"⚠️ 平倉後仍有持倉: {new_pos}")
            return False
            
    except Exception as e:
        add_log(f"平倉失敗: {e}")
        return False

# ============ trade logic ============
def run_grid_bot(exchange, info, coin):
    global last_center_price, running, last_report_time

    # 強制更新最新狀態
    if not update_status(info, coin):
        return

    with status_lock:
        mid_price = status_data["price"]
        current_pos = status_data["position"]
        pnl = status_data["pnl"]
        account_value = status_data["account_value"]

    if mid_price == 0:
        return

    # 檢查是否達到持倉上限
    if abs(current_pos) >= MAX_POSITION_SIZE:
        add_log(f"🎯 達持倉上限 {current_pos:.3f}/{MAX_POSITION_SIZE} YZY，平倉中...")
        
        if close_position(exchange, info, coin):
            send_tg_msg(f"🎯 {coin} 達持倉上限，已平倉\n持倉: {current_pos:+.3f} YZY\nPnL: {pnl:+.2f}")
            last_center_price = 0  # 成功平倉才重置
        return

    # 價格變動檢查
    if last_center_price != 0:
        deviation = abs(mid_price - last_center_price) / last_center_price
        if deviation < UPDATE_THRESHOLD:
            add_log(f"⏸️ 變動 {deviation:.3%} < {UPDATE_THRESHOLD:.2%}")
            return
    else:
        deviation = 0

    add_log(f"🔄 更新網格 @ {mid_price:.5f} (變動{deviation:.3%})")

    # 取消舊訂單
    cancel_count = cancel_all_orders(exchange, info, coin)
    if cancel_count > 0:
        time.sleep(0.5)

    # 再次確認最新持倉（取消訂單期間可能成交）
    update_status(info, coin)
    with status_lock:
        current_pos = status_data["position"]
    
    # 再次檢查持倉（避免取消訂單時成交導致超限）
    if abs(current_pos) >= MAX_POSITION_SIZE:
        add_log(f"⚠️ 取消訂單期間達上限，不掛新單")
        return

    # 計算網格
    lower = mid_price * (1 - GRID_RANGE_PCT)
    upper = mid_price * (1 + GRID_RANGE_PCT)
    step = (upper - lower) / (GRID_LEVELS - 1)

    # 生成訂單
    new_orders = []
    buy_count = sell_count = 0
    
    for i in range(GRID_LEVELS):
        px = round(lower + i * step, 5)
        if abs(px - mid_price) / mid_price < 0.001:
            continue
            
        is_buy = px < mid_price
        # 使用最新持倉計算
        new_pos = current_pos + (GRID_QUANTITY if is_buy else -GRID_QUANTITY)
        
        if abs(new_pos) <= MAX_POSITION_SIZE:
            new_orders.append({
                "coin": coin,
                "is_buy": is_buy,
                "sz": GRID_QUANTITY,
                "limit_px": px,
                "order_type": {"limit": {"tif": "Gtc"}},
                "reduce_only": False
            })
            if is_buy:
                buy_count += 1
            else:
                sell_count += 1

    # orders
    if new_orders:
        try:
            response = exchange.bulk_orders(new_orders)
            if response.get('status') == 'ok':
                last_center_price = mid_price
                add_log(f"✅ {len(new_orders)} 筆訂單 (買{buy_count}/賣{sell_count})")
            else:
                error = response.get('response', {}).get('error', '未知錯誤')
                add_log(f"❌ 下單失敗: {error}")
        except Exception as e:
            add_log(f"⚠️ 下單異常: {e}")
    else:
        add_log("⚠️ 無符合條件的訂單")

    # report
    now = time.time()
    if now - last_report_time >= REPORT_INTERVAL:
        record_daily_pnl(pnl)
        total_7d = get_7day_total_pnl()
        send_tg_msg(
            f"YZY grid report\n"
            f"position: {current_pos:+.3f} YZY\n"
            f"YZY price: {mid_price:.5f}\n"
            f"PnL: {pnl:+.2f} USD\n"
            f"account vaule: {account_value:.2f} USDC"
        )
        last_report_time = now

# ============ main ============
def main_logic():
    global running, last_report_time
    add_log(" YZY 網格機器人啟動")
    last_report_time = 0

    account = eth_account.Account.from_key(PRIVATE_KEY.strip())
    info = Info(constants.MAINNET_API_URL, skip_ws=True)
    exchange = Exchange(account, constants.MAINNET_API_URL)

    while running:
        try:
            run_grid_bot(exchange, info, COIN)
            time.sleep(UPDATE_INTERVAL)
        except KeyboardInterrupt:
            running = False
            add_log("👋 手動結束")
        except Exception as e:
            add_log(f"❌ 主程式錯誤: {e}")
            time.sleep(60)

if __name__ == "__main__":
    if 'PYTHONANYWHERE' in os.environ or not sys.stdout.isatty():
        main_logic()
    else:
        def curses_main(stdscr):
            draw_thread = threading.Thread(target=draw_screen, args=(stdscr,), daemon=True)
            draw_thread.start()
            main_logic()
        curses.wrapper(curses_main)
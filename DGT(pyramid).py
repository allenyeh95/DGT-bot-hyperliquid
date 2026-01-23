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
ACCOUNT_ADDRESS = "  your hyperliquid address  "
PRIVATE_KEY = " your private key  "
TG_TOKEN = "  optional "
TG_CHAT_ID = "optional"
COIN = "ETH"

# ============ 參數 ============
UPDATE_THRESHOLD, LIQUIDATION_PCT = 0.0035, 0.01
GRID_LEVELS, GRID_RANGE_PCT = 21, 0.01
UPDATE_INTERVAL = 15
MAX_POSITION_SIZE = 1.2
REPORT_INTERVAL = 3600  
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
    except:
        pass

# ============ PNL 檔案管理 ============
def record_daily_pnl(current_pnl):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    filename = "ETH_pnl_history.txt"
    if os.path.exists(filename):
        with open(filename, "r") as f:
            lines = f.readlines()
            if lines and lines[-1].startswith(today):
                return
    with open(filename, "a") as f:
        f.write(f"{today},{current_pnl:.2f}\n")
    add_log(f"損益已存檔: {today} | {current_pnl:.2f} USD")

def get_7day_total_pnl():
    filename = "eth_pnl_history.txt"
    if not os.path.exists(filename): return 0.0
    try:
        with open(filename, "r") as f:
            lines = [l.strip() for l in f.readlines() if "," in l]
            last_7 = [float(l.split(",")[1]) for l in lines[-7:]]
            return sum(last_7)
    except:
        return 0.0

# ============ 更新狀態 ============
def update_status(info, coin):
    try:
        all_mids = info.all_mids()
        if coin not in all_mids or all_mids[coin] is None:
            add_log("無法從 all_mids 獲取價格")
            return
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

    except Exception as e:
        add_log(f"狀態更新失敗: {type(e).__name__}: {e}")

# ============ 日誌系統 ============
def add_log(msg):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    log_msg = f"[{timestamp}] {msg}"
    log_lines.append(log_msg)
    if len(log_lines) > log_max_lines:
        log_lines.pop(0)
    print(log_msg)  # 雲端模式下必須用 print 才能在 Always-on log 看到

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
        title = f" ETH網格機器人 [{time_str}] "
        stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
        stdscr.addstr(0, 0, title.center(w))
        stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)

        with status_lock:
            data = status_data.copy()

        pnl_color = 1 if data["pnl"] >= 0 else 2
        pos_color = 3 if data["position"] > 0 else (2 if data["position"] < 0 else 1)

        line1 = f"PnL: {data['pnl']:+.2f} USD ({data['pnl_pct']:+.2f}%)".ljust(30)
        line1 += f"POS: {data['position']:+.4f} ETH".ljust(25)
        line1 += f"PRICE: {data['price']:.1f}".ljust(20)
        line1 += f"Account Value: {data['account_value']:.1f} USDC"
        stdscr.addstr(1, 2, line1)
        stdscr.attron(curses.color_pair(pnl_color) | curses.A_BOLD)
        stdscr.addstr(1, 7, f"{data['pnl']:+.2f} USD ({data['pnl_pct']:+.2f}%)")
        stdscr.attroff(curses.color_pair(pnl_color) | curses.A_BOLD)
        stdscr.attron(curses.color_pair(pos_color))
        stdscr.addstr(1, 37, f"{data['position']:+.4f} ETH")
        stdscr.attroff(curses.color_pair(pos_color))

        stdscr.hline(2, 0, curses.ACS_HLINE, w)
        center = last_center_price
        liq_up = center * (1 + LIQUIDATION_PCT)
        liq_dn = center * (1 - LIQUIDATION_PCT)
        stdscr.addstr(2, 0, f"強平界限: {liq_dn:.1f} <───> {liq_up:.1f}", curses.color_pair(2) | curses.A_BOLD)

        stdscr.addstr(3, 0, "RECORD:".ljust(w))
        stdscr.hline(4, 0, curses.ACS_HLINE, w)

        start_line = max(0, len(log_lines) - (h - 6))
        for i, log in enumerate(log_lines[start_line:]):
            if 5 + i < h:
                stdscr.addstr(5 + i, 0, log[:w-1])

        stdscr.refresh()
        time.sleep(0.5)

# ============ 交易邏輯 ============
def run_grid_bot(exchange, info, coin):
    global last_center_price, running, last_report_time

    update_status(info, coin)
    with status_lock:
        mid_price = status_data["price"]
        current_pos = status_data["position"]
        pnl = status_data["pnl"]
        account_value = status_data["account_value"]

    now = time.time()
    if now - last_report_time >= REPORT_INTERVAL:
        record_daily_pnl(pnl)
        send_tg_msg(f"ETH \nAccount Value: {account_value:.1f} USDC\nETH PNL: {pnl:+.2f} USD\nPosition Size: {current_pos:+.4f} ETH")
        last_report_time = now

    if mid_price == 0:
        add_log("無法獲取價格，跳過本次")
        return

    is_first = last_center_price == 0
    deviation = 0.0
    if not is_first:
        deviation = abs(mid_price - last_center_price) / last_center_price
        if deviation >= LIQUIDATION_PCT:
            add_log(f" 最後防線觸發 ({deviation:.2%})")
            exchange.market_close(coin)
            send_tg_msg(f" {coin}  {LIQUIDATION_PCT*100}% ")
            running = False
            return
        if deviation < UPDATE_THRESHOLD:
            add_log(f"Minimal deviation. ({deviation:.3%})")
            return

    add_log(f"🔄 Redeploying Grid @ {mid_price} (cuz{deviation:.3%})")

    try:
        open_orders = info.open_orders(ACCOUNT_ADDRESS)
        for o in open_orders:
            if o['coin'] == coin:
                exchange.cancel(coin, int(o['oid']))
        time.sleep(0.5)
    except Exception as e:
        add_log(f"撤單失敗: {e}")

    # 金字塔模式的單位定義
    BASE_UNIT = 0.001  # 基礎單位 A = 0.001 ETH  可調
    PYRAMID_LEVELS = {
        1: 5,   # 第1-3格: 5A
        2: 5,   # 第1-3格: 5A  
        3: 5,   # 第1-3格: 5A
        4: 6,   # 第4-6格: 6A
        5: 6,   # 第4-6格: 6A
        6: 6,   # 第4-6格: 6A
        7: 9,   # 第7-9格: 9A
        8: 9,   # 第7-9格: 9A
        9: 9,   # 第7-9格: 9A
        10: 20  # 第10格: 20A
    }

    # 計算網格參數
    lower = mid_price * (1 - GRID_RANGE_PCT)
    upper = mid_price * (1 + GRID_RANGE_PCT)
    step = (upper - lower) / (GRID_LEVELS - 1)

    # 找出中心格的位置
    center_idx = None
    min_diff = float('inf')
    for i in range(GRID_LEVELS):
        px = lower + i * step
        diff = abs(px - mid_price)
        if diff < min_diff:
            min_diff = diff
            center_idx = i

    new_orders = []
    for i in range(GRID_LEVELS):
        px = round(lower + i * step, 1)
        
        # 跳過太接近中心價的格
        if abs(px - mid_price) < 0.5:
            continue
        
        # 判斷買賣方向
        is_buy = px < mid_price
        
        # 計算距離中心價的格數（取絕對值）
        if center_idx is not None:
            distance = abs(i - center_idx)
        else:
            distance = abs((px - mid_price) / step)
        
        # 根據距離決定金字塔等級（1-10）
        pyramid_level = min(int(distance) + 1, 10)
        
        # 獲取對應的單位倍數
        unit_multiplier = PYRAMID_LEVELS.get(pyramid_level, 5)  # 默認5A
        
        # 計算數量
        qty = unit_multiplier * BASE_UNIT
        
        # 根據持倉方向調整數量（保持原有邏輯）
        if current_pos > 0.75:
            # 多單較大時，減小買單，增大賣單
            if is_buy:
                qty = max(qty * 0.7, 0.001)  # 最小0.001 ETH
            else:
                qty = qty * 1.3
        elif current_pos < -0.75:
            # 空單較大時，增大買單，減小賣單
            if is_buy:
                qty = qty * 1.3
            else:
                qty = max(qty * 0.7, 0.001)
        
        # 檢查是否超過最大持倉限制
        potential_pos = current_pos + (qty if is_buy else -qty)
        if abs(potential_pos) > MAX_POSITION_SIZE:
            add_log(f"⚠ 跳過訂單: 潛在持倉 {potential_pos:.4f} 超過限制")
            continue
        
        new_orders.append({
            "coin": coin,
            "is_buy": is_buy,
            "sz": round(qty, 4),  # 保留4位小數
            "limit_px": px,
            "order_type": {"limit": {"tif": "Gtc"}},
            "reduce_only": False
        })

    if new_orders:
        add_log(f"📊 金字塔模式: 發送 {len(new_orders)} 筆訂單")
        add_log(f"  基礎單位: {BASE_UNIT} ETH, 等級: 5A/6A/9A/20A")
        
        try:
            response = exchange.bulk_orders(new_orders)
            if response.get('status') == 'ok':
                last_center_price = mid_price
                add_log(f"✅ 金字塔網格已更新 (中心價: {mid_price:.1f})")
            else:
                add_log(f"❌ 下單失敗: {response}")
        except Exception as e:
            add_log(f"發送訂單異常: {e}")
    else:
        add_log("⚠ 沒有可生成的訂單 (可能超過持倉限制)")

    last_center_price = mid_price

# ============ 主要執行邏輯（不依賴 curses） ============
def main_logic():
    global running, last_report_time
    add_log(" ETH 網格機器人啟動！ (雲端模式)")
    add_log(f"📂 工作目錄: {os.getcwd()}")
    last_report_time = 0

    account = eth_account.Account.from_key(PRIVATE_KEY.strip())
    info = Info(constants.MAINNET_API_URL, skip_ws=True)
    exchange = Exchange(account, constants.MAINNET_API_URL)

    try:
        while running:
            run_grid_bot(exchange, info, COIN)
            time.sleep(UPDATE_INTERVAL)
    except KeyboardInterrupt:
        running = False
        add_log("手動停止，結束程式")
    except Exception as e:
        add_log(f"程式異常終止: {e}")
        running = False

# ============ 程式入口 ============
if __name__ == "__main__":
    
    if 'PYTHONANYWHERE' in os.environ or not sys.stdout.isatty():
        # 雲端模式：直接跑邏輯，不用 curses
        main_logic()
    else:
        
        def curses_main(stdscr):
            draw_thread = threading.Thread(target=draw_screen, args=(stdscr,), daemon=True)
            draw_thread.start()
            main_logic()  
        curses.wrapper(curses_main)

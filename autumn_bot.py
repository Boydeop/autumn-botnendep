import os
import logging
import requests
import time
import re
from datetime import datetime, timedelta
from io import BytesIO
import pandas as pd
import matplotlib
# Fix lỗi GUI trên server (VPS/Docker không có màn hình)
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
import mplfinance as mpf
from dotenv import load_dotenv

# Code chạy tốt nhất với python-telegram-bot v13.15
from telegram import ParseMode, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler

# Tải biến môi trường
load_dotenv()

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
DEFAULT_TRACK = os.environ.get("DEFAULT_TRACK", "bitcoin").lower() 
AUTO_PUSH_INTERVAL_MIN = int(os.environ.get("AUTO_PUSH_INTERVAL_MIN", "30")) # Mặc định 30 phút
PUSH_CHAT_ID_FILE = "push_chat_id.txt"

# Tỷ giá dự phòng (Fallback) nếu API lỗi
USDT_VND_RATE_FALLBACK = 25750 

# Cache settings (Giây)
CACHE_TTL_PRICE = 60      # Cache giá coin: 1 phút
CACHE_TTL_CHART = 600     # Cache biểu đồ: 10 phút
CACHE_TTL_RATE = 900      # Cache tỷ giá USD/VND: 15 phút
# ----------------------------------------

# --- TỪ ĐIỂN ÁNH XẠ & CONTRACT ƯU TIÊN ---
CONTRACT_MONAD = '0x3bd359C1119dA7Da1D913D1C4D2B7c461115433A'

COMMON_COINS = {
    'mon': CONTRACT_MONAD,
    'monad': CONTRACT_MONAD,
    'btc': 'bitcoin',
    'eth': 'ethereum',
    'bnb': 'binancecoin',
    'sol': 'solana',
    'pepe': 'pepe'
}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==============================================================================
#                               CACHE SYSTEM
# ==============================================================================
class SimpleCache:
    def __init__(self):
        self._store = {}

    def get(self, key):
        if key in self._store:
            data, expiry = self._store[key]
            if time.time() < expiry:
                return data
            else:
                del self._store[key] # Xóa nếu hết hạn
        return None

    def set(self, key, value, ttl_seconds):
        self._store[key] = (value, time.time() + ttl_seconds)

    def clear(self):
        self._store = {}

# Khởi tạo Global Cache
bot_cache = SimpleCache()

# ==============================================================================
#                               HELPER FUNCTIONS
# ==============================================================================

def is_contract_address(input_str: str) -> bool:
    if not input_str: return False
    return (input_str.startswith('0x') and len(input_str) == 42)

def resolve_coin_id(user_input: str) -> str:
    clean = user_input.lower().strip()
    if clean in COMMON_COINS: return COMMON_COINS[clean]
    return clean

def get_usdt_vnd_rate():
    """Lấy tỷ giá USDT/VND thực tế từ CoinGecko (Có Cache)"""
    cache_key = "rate_usdt_vnd"
    cached = bot_cache.get(cache_key)
    if cached: return cached

    try:
        # Lấy giá Tether (USDT) đổi sang VND
        url = "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=vnd"
        r = requests.get(url, timeout=10)
        data = r.json()
        rate = data.get("tether", {}).get("vnd")
        
        if rate:
            bot_cache.set(cache_key, rate, CACHE_TTL_RATE)
            return rate
    except Exception as e:
        logger.error(f"Lỗi lấy tỷ giá: {e}")
    
    return USDT_VND_RATE_FALLBACK

def search_coingecko_id(keyword: str):
    """Tìm ID CoinGecko"""
    cache_key = f"search_cg_{keyword}"
    cached = bot_cache.get(cache_key)
    if cached: return cached

    try:
        url = "https://api.coingecko.com/api/v3/search"
        params = {"query": keyword}
        r = requests.get(url, params=params, timeout=10)
        coins = r.json().get("coins", [])
        if coins:
            result = (coins[0].get("id"), coins[0].get("name"))
            bot_cache.set(cache_key, result, 86400) # Cache 1 ngày
            return result
        return None, None
    except:
        return None, None

def search_dexscreener(keyword: str):
    try:
        url = f"https://api.dexscreener.com/latest/dex/search?q={keyword}"
        r = requests.get(url, timeout=10)
        pairs = r.json().get("pairs", [])
        if not pairs: return None
        return sorted(pairs, key=lambda x: x.get("liquidity", {}).get("usd", 0), reverse=True)[0]
    except:
        return None

def get_price_data(coin_id: str):
    """Tổng hợp giá: CoinGecko (Priority) -> DexScreener Contract -> DexScreener Search"""
    cache_key = f"price_{coin_id}"
    cached = bot_cache.get(cache_key)
    if cached: return cached

    result = None

    # Strategy 1: CoinGecko (Ưu tiên tra cứu ID trước)
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd&include_market_cap=true&include_24hr_change=true"
        r = requests.get(url, timeout=10)
        data = r.json().get(coin_id)
        if data:
            result = {
                "source": "cg",
                "id": coin_id,
                "price": data.get("usd"),
                "change": data.get("usd_24h_change"),
                "name": coin_id.upper(),
                "chain": "CEX/CG",
                "url": f"https://www.coingecko.com/en/coins/{coin_id}"
            }
    except Exception as e:
        logger.error(f"CG Error: {e}")
    
    # Strategy 2: DexScreener
    if not result and is_contract_address(coin_id):
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{coin_id}"
            r = requests.get(url, timeout=10)
            pairs = r.json().get("pairs") or []
            if pairs:
                pair = sorted(pairs, key=lambda x: x.get("liquidity", {}).get("usd", 0), reverse=True)[0]
                result = {
                    "source": "dex",
                    "id": coin_id,
                    "price": float(pair.get("priceUsd") or 0),
                    "change": pair.get("priceChange", {}).get("h24", 0),
                    "name": pair.get("baseToken", {}).get("symbol", "TOKEN"),
                    "chain": pair.get("chainId", "Unknown"),
                    "url": pair.get("url", "https://dexscreener.com")
                }
        except Exception as e:
            logger.error(f"Dex Error: {e}")

    # Strategy 3: Search Dex by Name
    if not result:
        pair = search_dexscreener(coin_id)
        if pair:
            result = {
                "source": "dex",
                "id": coin_id, # Lưu ý: đây có thể không phải ID chuẩn để refresh nếu là keyword search
                "price": float(pair.get("priceUsd", 0)),
                "change": pair.get("priceChange", {}).get("h24", 0),
                "name": pair.get("baseToken", {}).get("symbol", coin_id).upper(),
                "chain": pair.get("chainId", "Unknown"),
                "url": pair.get("url", "https://dexscreener.com")
            }

    if result:
        bot_cache.set(cache_key, result, CACHE_TTL_PRICE)
    
    return result

# --- CHART HELPERS ---

def get_ohlc_data(coin_id: str, days: str):
    try:
        d_int = int(days)
        valid_days = [1, 7, 14, 30, 90, 180, 365]
        mapped_day = str(min(valid_days, key=lambda x: abs(x - d_int)))
    except:
        mapped_day = "1"

    cache_key = f"ohlc_{coin_id}_{mapped_day}"
    cached_df = bot_cache.get(cache_key)
    if cached_df is not None: return cached_df

    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc?vs_currency=usd&days={mapped_day}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if not data or not isinstance(data, list): return None
        
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        bot_cache.set(cache_key, df, CACHE_TTL_CHART)
        return df
    except Exception as e:
        logger.error(f"OHLC Error: {e}")
        return None

def plot_candlestick(df, title):
    buf = BytesIO()
    mc = mpf.make_marketcolors(up='#0ecb81', down='#f6465d', inherit=True)
    s = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=mc)
    
    mpf.plot(
        df, type='candle', style=s, title=f"\n{title.upper()} Price",
        ylabel='Price ($)', datetime_format='%H:%M',
        tight_layout=True, savefig=dict(fname=buf, format='png', dpi=100)
    )
    buf.seek(0)
    return buf

# ==============================================================================
#                               COMMAND HANDLERS
# ==============================================================================

def start_cmd(update: Update, context: CallbackContext):
    txt = (
        "🍂 <b>AUTUMN BOT - PRO FINANCE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <code>/p mon</code> : Xem giá (Realtime + Button)\n"
        "• <code>/g 100u</code> : Quy đổi tỷ giá VND\n"
        "• <code>/ch btc 7d</code> : Vẽ biểu đồ nến\n"
        "• <code>/setpushchat</code> : Bật báo giá tự động\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    update.message.reply_text(txt, parse_mode=ParseMode.HTML)

def build_price_message(data):
    """Hàm tạo nội dung tin nhắn và nút bấm"""
    p = data['price']
    c = data['change']
    name = data['name']
    src = "DexScreener" if data['source'] == 'dex' else "CoinGecko"
    url = data.get('url', '')
    
    # Icon logic
    if c >= 10: icon = "🚀"
    elif c >= 0: icon = "🟢"
    elif c <= -10: icon = "🩸"
    else: icon = "🔴"

    p_fmt = f"{p:,.8f}" if p < 0.01 else f"{p:,.4f}"
    time_now = datetime.now().strftime("%H:%M:%S")

    # Nội dung dùng HTML cho đẹp
    text = (
        f"{icon} <b>{name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Price:</b> <code>${p_fmt}</code>\n"
        f"📉 <b>Change 24h:</b> <code>{c:+.2f}%</code>\n"
        f"🔗 <b>Chain:</b> {data.get('chain', 'N/A')}\n"
        f"📡 <b>Source:</b> {src}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>🕒 Updated: {time_now}</i>"
    )

    # Tạo Inline Keyboard (Nút bấm)
    keyboard = [
        [
            InlineKeyboardButton("🔄 Làm mới", callback_data=f"refresh|{data['id']}"),
            InlineKeyboardButton("↗️ Xem chi tiết", url=url) if url else None
        ]
    ]
    # Lọc bỏ None nếu không có url
    keyboard = [list(filter(None, row)) for row in keyboard]
    
    return text, InlineKeyboardMarkup(keyboard)

def price_cmd(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("💡 Dùng: `/p <tên_coin>`", parse_mode=ParseMode.MARKDOWN)
        return
    user_input = context.args[0].lower().strip()
    coin_id = resolve_coin_id(user_input)
    
    data = get_price_data(coin_id)
    
    if data:
        text, reply_markup = build_price_message(data)
        update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    else:
        update.message.reply_text(f"❌ Không tìm thấy: <b>{user_input}</b>", parse_mode=ParseMode.HTML)

def refresh_price_callback(update: Update, context: CallbackContext):
    """Xử lý sự kiện bấm nút Làm Mới"""
    query = update.callback_query
    query.answer("Đang cập nhật giá...") # Hiện thông báo nhỏ loading

    try:
        # Lấy coin_id từ callback_data (format: refresh|coin_id)
        _, coin_id = query.data.split("|", 1)
        
        # Xóa cache cũ để lấy giá mới nhất
        if f"price_{coin_id}" in bot_cache._store:
            del bot_cache._store[f"price_{coin_id}"]

        data = get_price_data(coin_id)
        if data:
            text, reply_markup = build_price_message(data)
            # Sửa tin nhắn cũ thành tin nhắn mới
            query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        else:
            query.edit_message_text(text="❌ Không thể cập nhật dữ liệu. Vui lòng thử lại lệnh /p.", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Refresh Error: {e}")
        query.edit_message_text(text="❌ Đã xảy ra lỗi khi làm mới.", parse_mode=ParseMode.HTML)

def chart_cmd(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("💡 Dùng: `/ch <symbol> [ngày]`", parse_mode=ParseMode.MARKDOWN)
        return
    
    user_input = context.args[0].lower().strip()
    days_arg = context.args[1] if len(context.args) > 1 else "1"
    days_clean = days_arg.lower().replace('d', '')
    if not days_clean.isdigit(): days_clean = "1"

    coin_id = resolve_coin_id(user_input)
    
    # Logic Map ID
    chart_id = coin_id
    display_name = user_input.upper()
    
    if is_contract_address(coin_id):
        msg = update.message.reply_text("🔍 Đang tìm Coin ID...")
        found_id, found_name = search_coingecko_id(user_input)
        if not found_id: found_id, found_name = search_coingecko_id(coin_id)
        
        if found_id:
            chart_id = found_id
            display_name = found_name
            msg.delete()
        else:
            msg.edit_text("❌ Token này chưa có dữ liệu biểu đồ.")
            return
    else:
        found_id, found_name = search_coingecko_id(user_input)
        if found_id: chart_id = found_id

    msg_wait = update.message.reply_text(f"📊 Đang vẽ nến <b>{display_name}</b>...", parse_mode=ParseMode.HTML)

    df = get_ohlc_data(chart_id, days_clean)
    if df is not None and not df.empty:
        try:
            img = plot_candlestick(df, display_name)
            msg_wait.delete()
            # Chart caption cũng dùng HTML cho đồng bộ
            caption = (
                f"🕯 <b>Chart: {display_name}</b> ({days_arg})\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Open: <code>{df.iloc[-1]['open']}</code>\n"
                f"Close: <code>{df.iloc[-1]['close']}</code>"
            )
            update.message.reply_photo(img, caption=caption, parse_mode=ParseMode.HTML)
        except Exception as e:
            msg_wait.edit_text(f"❌ Lỗi vẽ ảnh: {e}")
    else:
        msg_wait.edit_text(f"❌ Không có dữ liệu lịch sử nến.")

def convert_vnd_cmd(update: Update, context: CallbackContext):
    raw = "".join(context.args).lower().strip()
    current_rate = get_usdt_vnd_rate()

    if not raw:
        update.message.reply_text(f"💡 Dùng: <code>/g 100u</code>\n(Rate: {current_rate:,.0f} đ)", parse_mode=ParseMode.HTML)
        return

    match = re.match(r"([0-9.]+)([a-z0-9]+)", raw)
    if match:
        amount = float(match.group(1))
        symbol = match.group(2)
    else:
        try:
            amount = float(raw)
            symbol = 'usdt'
        except:
            update.message.reply_text("❌ Lỗi cú pháp."); return

    if symbol in ['u', 'usdt', 'usd']:
        coin_price_usd = 1
        coin_name = "USDT"
    else:
        data = get_price_data(resolve_coin_id(symbol))
        if not data:
             update.message.reply_text(f"❌ Ko thấy giá {symbol}"); return
        coin_price_usd = data['price']
        coin_name = data['name']

    total_vnd = amount * coin_price_usd * current_rate
    
    if total_vnd > 1_000_000_000:
        vnd_str = f"{total_vnd/1_000_000_000:,.2f} Tỷ"
    elif total_vnd > 1_000_000:
        vnd_str = f"{total_vnd/1_000_000:,.1f} Triệu"
    else:
        vnd_str = f"{total_vnd:,.0f} đ"

    rate_icon = "📈" if current_rate > USDT_VND_RATE_FALLBACK else "📉"

    # Layout thẻ đẹp cho quy đổi
    msg = (
        f"🇻🇳 <b>BẢNG QUY ĐỔI GIÁ TRỊ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>Input:</b> <code>{amount:g} {coin_name}</code>\n"
        f"💵 <b>Giá USD:</b> <code>${coin_price_usd:,.4f}</code>\n"
        f"🔄 <b>Tỷ giá:</b> <code>{current_rate:,.0f} VND</code> {rate_icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>THÀNH TIỀN:</b>\n"
        f"👉 <b>{vnd_str}</b>"
    )

    update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# --- AUTO PUSH JOB ---
def get_push_chat_id():
    if os.path.exists(PUSH_CHAT_ID_FILE):
        with open(PUSH_CHAT_ID_FILE, "r") as f:
            return f.read().strip()
    return None

def setpushchat_cmd(update: Update, context: CallbackContext):
    with open(PUSH_CHAT_ID_FILE, "w") as f:
        f.write(str(update.message.chat_id))
    update.message.reply_text(f"✅ Đã set kênh báo giá!\nTrack: <b>{DEFAULT_TRACK.upper()}</b>", parse_mode=ParseMode.HTML)

def auto_push_job(context: CallbackContext):
    chat_id = get_push_chat_id()
    if not chat_id: return

    data = get_price_data(DEFAULT_TRACK)
    if data:
        p = data['price']
        c = data['change']
        icon = "🚀" if c >= 5 else ("🟢" if c >= 0 else "🔻")
        
        # Dùng format HTML cho auto push luôn
        msg = (
            f"🔔 <b>AUTO UPDATE: {data['name']}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{icon} Price: <code>${p:,.4f}</code>\n"
            f"📉 Change: <code>{c:+.2f}%</code>"
        )
        try:
            # Auto push có thể thêm nút Refresh cũng được, nhưng ở đây để đơn giản chỉ gửi text
            context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Push Error: {e}")

# ==============================================================================
#                               MAIN
# ==============================================================================
def main():
    if not TELEGRAM_TOKEN:
        print("❌ Vui lòng set TELEGRAM_TOKEN trong .env")
        return

    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    jq = updater.job_queue

    # Handlers
    dp.add_handler(CommandHandler(["start", "help"], start_cmd))
    dp.add_handler(CommandHandler(["price", "p"], price_cmd))
    dp.add_handler(CommandHandler(["chart", "ch"], chart_cmd))
    dp.add_handler(CommandHandler(["g", "gia"], convert_vnd_cmd))
    dp.add_handler(CommandHandler("setpushchat", setpushchat_cmd))
    
    # Handler cho nút bấm (CallbackQuery)
    # pattern='^refresh\|' nghĩa là bắt các sự kiện có data bắt đầu bằng "refresh|"
    dp.add_handler(CallbackQueryHandler(refresh_price_callback, pattern='^refresh\|'))

    # Auto Push
    interval_sec = AUTO_PUSH_INTERVAL_MIN * 60
    jq.run_repeating(auto_push_job, interval=interval_sec, first=10)

    logger.info(f"Bot Started! Auto push every {AUTO_PUSH_INTERVAL_MIN} mins.")
    
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
🍂 Autumn Bot

Bot Telegram theo dõi giá Crypto, vẽ biểu đồ nến và quy đổi tỷ giá VNĐ theo thời gian thực.

Tính năng

Xem giá (Realtime): Hỗ trợ CoinGecko và DexScreener (ưu tiên Contract/Meme coin).

Vẽ biểu đồ: Biểu đồ nến (Candlestick) chuyên nghiệp.

Quy đổi tiền tệ: /g 100u tự động lấy tỷ giá USDT/VND thị trường chợ đen.

Auto Push: Tự động báo giá Bitcoin (hoặc coin tùy chọn) vào nhóm mỗi 30 phút.

Cache System: Tối ưu tốc độ, hạn chế spam API.

Cài đặt (Chạy Local)

Clone repo:

git clone [https://github.com/yourname/autumn-bot.git](https://github.com/yourname/autumn-bot.git)
cd autumn-bot


Cài đặt thư viện:

pip install -r requirements.txt


Cấu hình:

Đổi tên file .env.example thành .env

Nhập TELEGRAM_TOKEN của bạn vào.

Chạy bot:

python autumn_bot.py


Cài đặt (Docker)

Chỉ cần 1 lệnh duy nhất nếu đã cài Docker:

docker build -t autumn-bot . && docker run -d --env-file .env autumn-bot


Danh sách lệnh

Lệnh

Mô tả

Ví dụ

/p

Xem giá token

/p mon, /p 0x...

/ch

Vẽ biểu đồ nến

/ch btc 7d

/g

Quy đổi sang VNĐ

/g 100u, /g 1eth

/setpushchat

Set kênh báo giá tự động



Cấu trúc thư mục

autumn_bot.py: Main logic.

requirements.txt: Dependencies.

Dockerfile: Config cho Docker deploy.
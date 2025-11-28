# Sử dụng Python 3.9 bản nhẹ (Slim)
FROM python:3.9-slim

# Thiết lập thư mục làm việc
WORKDIR /app

# Copy file requirements trước để tận dụng cache của Docker
COPY requirements.txt .

# Cài đặt thư viện
# --no-cache-dir giúp giảm dung lượng image
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ code vào
COPY . .

# Lệnh chạy bot
CMD ["python", "autumn_bot.py"]
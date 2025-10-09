# ใช้ base image ของ Python
FROM python:3.10-slim

# ตั้ง working directory
WORKDIR /app

# คัดลอกไฟล์ทั้งหมดเข้า container
COPY . .

# ติดตั้ง dependencies
RUN pip install --no-cache-dir -r requirements.txt

# เปิด port 8501 สำหรับ Streamlit
EXPOSE 8501

# รัน Streamlit app
CMD ["streamlit", "run", "app_songkla.py", "--server.port=8501", "--server.address=0.0.0.0"]

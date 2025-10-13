FROM apache/airflow:2.9.3

ARG AIRFLOW_VERSION=2.9.3
ARG PYTHON_VERSION=3.10
ARG CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

# สลับเป็น root เพื่อติดตั้ง dependencies ของ Chrome และ libgomp1
USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium-driver \
    chromium \
    xvfb \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# กลับมาเป็น user airflow (ตามมาตรฐานของ Apache Airflow)
USER airflow

# Copy requirements.txt ไปไว้ใน container (โฟลเดอร์เดียวกับ Dockerfile)
COPY requirements.txt .

# ติดตั้ง Python packages พร้อม constraint ของ Airflow
RUN pip install --no-cache-dir -r requirements.txt --constraint "${CONSTRAINT_URL}"

# Copy โค้ด DAGs และ tasks เข้าไป
COPY dags/ /opt/airflow/dags/
COPY dags/tasks/ /opt/airflow/dags/tasks/
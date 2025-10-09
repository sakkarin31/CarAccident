FROM apache/airflow:2.9.2

# ตั้งค่า argument สำหรับ constraint
ARG AIRFLOW_VERSION=2.9.2
ARG PYTHON_VERSION=3.10
ARG CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

USER airflow

# ติดตั้ง dependencies ที่ใช้ใน DAG
RUN pip install --no-cache-dir pandas prophet matplotlib plotly -c "${CONSTRAINT_URL}"

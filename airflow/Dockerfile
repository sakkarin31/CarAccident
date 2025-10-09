FROM apache/airflow:2.9.2

# ตั้งค่า argument สำหรับ constraint
ARG AIRFLOW_VERSION=2.9.2
ARG PYTHON_VERSION=3.12
ARG CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

# ใช้ user airflow (ค่า default อยู่แล้ว แต่เขียนชัดเจนไว้)
USER airflow

# ติดตั้ง dependencies (prophet + pandas + matplotlib)
RUN pip install --no-cache-dir pandas prophet matplotlib plotly -c "${CONSTRAINT_URL}"



import os
import csv
from datetime import datetime
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.utils.dates import days_ago
import logging

logger = logging.getLogger(__name__)

# --- CONFIG ---
PG_CONN_ID = 'postgres_default'
TARGET_TABLE = 'songkhla_daily_clean'

# หา path ของไฟล์ CSV
DAGS_FOLDER = os.path.dirname(__file__)
CSV_FILE_PATH = os.path.join(DAGS_FOLDER, 'data', 'cleandaily-all-years.csv')

# ------------------------------
# CREATE TABLE IF NOT EXISTS
# ------------------------------
def create_target_table_if_not_exists():
    """สร้างตาราง songkhla_daily_clean หากยังไม่มี"""
    hook = PostgresHook(PG_CONN_ID)
    hook.run(f"""
        CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
            date              DATE PRIMARY KEY,
            temperature_f     DOUBLE PRECISION,
            humidity_pct      DOUBLE PRECISION,
            pressure_in       DOUBLE PRECISION,
            accidents         INTEGER,
            vehicles_lt_4     INTEGER,
            vehicles_eq_4     INTEGER,
            vehicles_gt_4     INTEGER,
            day_of_week       SMALLINT,
            is_weekend        BOOLEAN,
            is_holiday        BOOLEAN,
            year              INTEGER,
            created_at        TIMESTAMP DEFAULT NOW()
        );
    """)
    logger.info(f"✅ ตาราง '{TARGET_TABLE}' ถูกตรวจสอบ/สร้างเรียบร้อยแล้ว")

# ------------------------------
# LOAD CSV TO POSTGRES
# ------------------------------
def load_csv_to_postgres():
    """อ่านไฟล์ CSV และ insert ลง PostgreSQL"""
    pg_hook = PostgresHook(PG_CONN_ID)
    conn = pg_hook.get_conn()
    cursor = conn.cursor()

    with open(CSV_FILE_PATH, mode='r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            try:
                cursor.execute(f"""
                    INSERT INTO {TARGET_TABLE} (
                        date, temperature_f, humidity_pct, pressure_in,
                        accidents, vehicles_lt_4, vehicles_eq_4, vehicles_gt_4,
                        day_of_week, is_weekend, is_holiday, year
                    )
                    VALUES (
                        %(date)s, %(temperature_f)s, %(humidity_pct)s, %(pressure_in)s,
                        %(accidents)s, %(vehicles_lt_4)s, %(vehicles_eq_4)s, %(vehicles_gt_4)s,
                        %(day_of_week)s, %(is_weekend)s::BOOLEAN, %(is_holiday)s::BOOLEAN, %(year)s
                    )
                    ON CONFLICT (date) DO NOTHING;
                """, {
                    'date': row['datetime'],
                    'temperature_f': float(row['temperature_F']),
                    'humidity_pct': float(row['humidity_%']),
                    'pressure_in': float(row['pressure_in']),
                    'accidents': int(float(row['เกิดเหตุ'])),
                    'vehicles_lt_4': int(float(row['vehicles_lt_4_wheels'])),
                    'vehicles_eq_4': int(float(row['vehicles_4_wheels'])),
                    'vehicles_gt_4': int(float(row['vehicles_gt_4_wheels'])),
                    'day_of_week': int(row['day_of_week']),
                    'is_weekend': 'true' if int(row['is_weekend']) == 1 else 'false',
                    'is_holiday': 'true' if int(row['is_holiday']) == 1 else 'false',
                    'year': int(row['year'])
                })
            except Exception as e:
                logger.error(f"❌ ข้อผิดพลาดที่แถว {reader.line_num}: {row}")
                logger.exception(e)
                raise

    conn.commit()
    cursor.close()
    conn.close()
    logger.info(f"✅ โหลดข้อมูลจาก CSV เข้าสู่ตาราง '{TARGET_TABLE}' เรียบร้อยแล้ว")

# ------------------------------
# DAG Definition
# ------------------------------
default_args = {
    'owner': 'airflow',
    'start_date': days_ago(1),
}

dag = DAG(
    'load_daily_dataset_to_postgres',
    default_args=default_args,
    description='โหลดข้อมูล daily จาก CSV เข้า PostgreSQL',
    schedule_interval=None,
    catchup=False,
)

create_table_task = PythonOperator(
    task_id='create_target_table',
    python_callable=create_target_table_if_not_exists,
    dag=dag,
)

load_data_task = PythonOperator(
    task_id='load_csv_to_postgres',
    python_callable=load_csv_to_postgres,
    dag=dag,
)

create_table_task >> load_data_task

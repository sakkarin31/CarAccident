from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta
import os
import subprocess

# -----------------------------
# Path configuration
# -----------------------------
DAGS_FOLDER = os.path.dirname(__file__)
TASKS_FOLDER = os.path.join(DAGS_FOLDER, "tasks")
SPARK_FOLDER = "/opt/airflow/spark/application/spark_jobs"  # ตำแหน่งของ lstm.py และ predict_acc.py

# -----------------------------
# Helper function to run Python scripts
# -----------------------------
def run_script(script_path):
    print(f"🚀 Running script: {script_path}")
    result = subprocess.run(["python", script_path], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError(f"❌ Script failed: {script_path}")
    print(f"✅ Completed: {os.path.basename(script_path)}")


# -----------------------------
# Default args
# -----------------------------
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=2),
}

# -----------------------------
# Define DAG
# -----------------------------
with DAG(
    dag_id='weather_forecast_pipeline',
    default_args=default_args,
    description='Pipeline: last30_weather -> usage_current -> lstm -> predict_acc',
    schedule_interval=None,  # หรือ '0 6 * * *' เพื่อรันทุกวันตอน 6 โมงเช้า
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['forecast', 'xgboost', 'accident', 'weather'],
) as dag:

    # ---- Task 1: last30_weather.py ----
    task_last30 = PythonOperator(
        task_id='run_last30_weather',
        python_callable=run_script,
        op_args=[os.path.join(TASKS_FOLDER, "last30_weather.py")],
    )

    # ---- Task 2: usage_current.py ----
    task_usage = PythonOperator(
        task_id='run_usage_current',
        python_callable=run_script,
        op_args=[os.path.join(TASKS_FOLDER, "usage_current.py")],
    )

    # ---- Task 3: lstm.py ----
    task_lstm = PythonOperator(
        task_id='run_lstm_model',
        python_callable=run_script,
        op_args=[os.path.join(SPARK_FOLDER, "lstm.py")],
    )

    # ---- ✅ Task 4: predict_acc.py ----
    task_predict_acc = PythonOperator(
        task_id='run_predict_acc',
        python_callable=run_script,
        op_args=[os.path.join(SPARK_FOLDER, "predict_acc.py")],
    )

    # -----------------------------
    # Pipeline order
    # -----------------------------
    task_last30 >> task_usage >> task_lstm >> task_predict_acc

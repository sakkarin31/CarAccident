from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime

from tasks.check_file_task import check_file_exists
from tasks.extract_task import extract
from tasks.transform_task import transform
from tasks.forecast_task import forecast_7days
from tasks.load_task import load

default_args = {
    'owner': 'airflow',
    'start_date': datetime(2025, 9, 1),
}

with DAG(
    dag_id='timeseries_pipeline',
    default_args=default_args,
    schedule_interval='@daily',
    catchup=False
) as dag:

    t0 = PythonOperator(task_id='check_file_exists', python_callable=check_file_exists)
    t1 = PythonOperator(task_id='extract', python_callable=extract)
    t2 = PythonOperator(task_id='transform', python_callable=transform)
    t3 = PythonOperator(task_id='forecast_7days', python_callable=forecast_7days)
    t4 = PythonOperator(task_id='load', python_callable=load)

    t0 >> t1 >> t2 >> t3 >> t4

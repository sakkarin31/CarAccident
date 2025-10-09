import pandas as pd

def load(**kwargs):
    print("📥 Starting Load Task ...")

    ti = kwargs["ti"]
    data = ti.xcom_pull(task_ids="forecast_7days")

    if not data:
        raise ValueError("❌ No forecast data received!")

    df = pd.DataFrame(data)

    output_path = "/opt/airflow/data/processed/forecast.csv"
    df.to_csv(output_path, index=False)

    print(f"✅ Forecast saved to {output_path}")
    print(df)

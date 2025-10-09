import pandas as pd
from prophet import Prophet
import matplotlib.pyplot as plt
import os

def forecast_7days(**kwargs):
    print("📈 Starting Prophet forecast ...")

    # 1) ดึงข้อมูลจาก XCom (task transform)
    ti = kwargs["ti"]
    data = ti.xcom_pull(task_ids="transform")

    if not data:
        raise ValueError("❌ No data received from transform task!")

    # 2) สร้าง DataFrame
    df = pd.DataFrame(data)

    # 3) แปลง datatype
    df["ds"] = pd.to_datetime(df["ds"])
    df["y"] = pd.to_numeric(df["y"], errors="coerce")

    print("📊 Data received for forecast:")
    print(df.head())

    # 4) Train Prophet
    model = Prophet(daily_seasonality=True)
    model.fit(df)

    # 5) Forecast 7 วันถัดไป
    future = model.make_future_dataframe(periods=7)
    forecast = model.predict(future)

    # 6) Log ผลลัพธ์
    print("✅ Forecasted values (last 10 rows):")
    print(forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(10))

    # 7) Save forecast เป็น CSV
    output_dir = "/opt/airflow/data/processed"
    os.makedirs(output_dir, exist_ok=True)
    forecast_path = os.path.join(output_dir, "forecast.csv")
    forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].to_csv(forecast_path, index=False)
    print(f"📁 Forecast CSV saved to {forecast_path}")

    # 8) Save plot เป็น PNG
    fig = model.plot(forecast)
    plt.title("Prophet Forecast (Close Price)")
    plt.xlabel("Date")
    plt.ylabel("Close")
    plt.tight_layout()

    forecast_plot = os.path.join(output_dir, "forecast_plot.png")
    fig.savefig(forecast_plot)
    print(f"📊 Forecast plot saved to {forecast_plot}")

    # 9) Return 7 วันถัดไป
    result = forecast[["ds", "yhat"]].tail(7).copy()
    result["ds"] = result["ds"].astype(str)  # serialize ได้
    return result.to_dict(orient="records")

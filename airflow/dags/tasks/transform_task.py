import pandas as pd

def transform(**kwargs):
    file_path = "/opt/airflow/data/raw/raw_sales.csv"
    print(f"🔄 Transforming data from {file_path} ...")

    df = pd.read_csv(file_path)

    # ใช้เฉพาะ date + close
    df = df[["datesold", "price"]].copy()
    df.rename(columns={"datesold": "ds", "price": "y"}, inplace=True)
    df["ds"] = pd.to_datetime(df["ds"])


    print("✅ Transformed Data:")
    print(df.head())

    # ✅ แปลง datetime เป็น string ก่อนส่งคืน
    df["ds"] = df["ds"].astype(str)
    return df.to_dict(orient="records")

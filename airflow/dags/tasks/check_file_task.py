import os
import pandas as pd

def check_file_exists(**kwargs):
    file_path = "/opt/airflow/data/raw/raw_sales.csv"

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"❌ File not found: {file_path}")

    df = pd.read_csv(file_path)

    print("📊 Columns in CSV:", list(df.columns))
    print("🔍 First rows:\n", df.head())

    required_cols = ["datesold", "price"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"❌ Missing required column: {col}")

        if df["datesold"].isnull().any() or df["price"].isnull().any():
            raise ValueError("❌ Null values found in 'datesold' or 'price' columns")


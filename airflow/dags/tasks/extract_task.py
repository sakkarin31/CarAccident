import pandas as pd

def extract(**kwargs):
    file_path = "/opt/airflow/data/raw/raw_sales.csv"  # 👈 เปลี่ยนเป็นไฟล์จริง
    print(f"Extracting data from {file_path} ...")

    df = pd.read_csv(file_path)
    print("✅ Extracted rows:", len(df))
    print(df.head())

    # ส่งต่อไปยัง XCom (optional)
    return file_path

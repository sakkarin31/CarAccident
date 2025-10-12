# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
from pathlib import Path

# CONFIG
BASE = Path(".")
YEARS = [2020, 2021, 2022, 2023, 2024]          # ปีที่อยากประมวลผล
IN_FMT  = "datafinal-{year}-perday.csv"         # ไฟล์อินพุตราย 30 นาที (มี date,time,...)
OUT_FMT = "cleandaily-{year}.csv"               # เอาต์พุตรายวันต่อปี
SAVE_COMBINED = True                            # รวมทุกปีเป็นไฟล์เดียวด้วยไหม
OUT_COMBINED  = "cleandaily-all-years.csv"      # ชื่อไฟล์รวมทุกปี (ถ้า SAVE_COMBINED=True)

DROP_COLS = ["condition", "รถน้อยกว่า4ล้อacc", "รถ4ล้อacc", "รถมากกว่า4ล้อacc"]
NUMERIC_COLS = [
    "temperature_F","humidity_%","wind_speed_kmh","pressure_in",
    "เกิดเหตุ","vehicles_lt_4_wheels","vehicles_4_wheels","vehicles_gt_4_wheels"
]

# รูปแบบวันที่เวลาในไฟล์อินพุต (ของคุณเป็น MM/DD/YYYY + 12h AM/PM)
# ถ้าบางปี format เพี้ยน โค้ดจะ fallback ไป parse อัตโนมัติ
FIXED_DT_FORMAT = "%m/%d/%Y %I:%M %p"

def read_csv_smart(path: Path):
    last_err = None
    for enc in ("utf-8-sig","utf-8","cp874","iso-8859-11"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as e:
            last_err = e
    raise last_err

def coerce_numeric(df: pd.DataFrame, cols):
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def daily_agg_one_year(in_path: Path, out_path: Path):
    if not in_path.exists():
        print(f"ไม่พบไฟล์: {in_path.name} (ข้าม)")
        return None

    df = read_csv_smart(in_path)

    # รวม date+time -> datetime
    dt_str = df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip()
    try:
        df["datetime"] = pd.to_datetime(dt_str, format=FIXED_DT_FORMAT, errors="raise")
    except Exception:
        df["datetime"] = pd.to_datetime(dt_str, errors="coerce")

    bad = df["datetime"].isna().sum()
    if bad:
        raise ValueError(f"{in_path.name}: แปลง datetime ไม่ได้ {bad} แถว — โปรดตรวจไฟล์ต้นทาง")

    # ลบคอลัมน์ที่ไม่ใช้ (ถ้ามี)
    df = df.drop(columns=[c for c in DROP_COLS if c in df.columns])

    # บังคับเป็นตัวเลขกัน mean/sum ล้มเหลว
    df = coerce_numeric(df, NUMERIC_COLS)

    # รวมรายวัน
    daily_df = (
        df.groupby(df["datetime"].dt.normalize())  # ได้เวลา 00:00 ของแต่ละวัน
          .agg({
               "temperature_F": "mean",
               "humidity_%":    "mean",
               "wind_speed_kmh": "mean",
               "pressure_in":   "mean",
               "เกิดเหตุ":            "sum",
               "vehicles_lt_4_wheels": "sum",
               "vehicles_4_wheels":    "sum",
               "vehicles_gt_4_wheels": "sum"
           })
          .reset_index()
          .rename(columns={"datetime":"datetime"})  # คอลัมน์ชื่อ 'datetime' อยู่แล้ว
    )

    # Features วัน
    daily_df["day_of_week"] = daily_df["datetime"].dt.dayofweek
    daily_df["is_weekend"]  = daily_df["day_of_week"].isin([5,6]).astype(int)

    # วันหยุดไทย (อิงปีจากข้อมูลจริงใน daily_df)
    try:
        import holidays
        years = sorted(set(daily_df["datetime"].dt.year))
        th = holidays.TH(years=years, observed=True)
        daily_df["is_holiday"] = daily_df["datetime"].dt.date.isin(th).astype(int)
    except Exception:
        daily_df["is_holiday"] = 0

    # จัดลำดับคอลัมน์
    cols_out = [
        "datetime","เกิดเหตุ","temperature_F","humidity_%","wind_speed_kmh","pressure_in",
        "vehicles_lt_4_wheels","vehicles_4_wheels","vehicles_gt_4_wheels",
        "day_of_week","is_weekend","is_holiday"
    ]
    # เผื่อบางคอลัมน์ไม่อยู่ (จะไม่พัง)
    cols_out = [c for c in cols_out if c in daily_df.columns]

    daily_df[cols_out].to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"บันทึกไฟล์เรียบร้อย: {out_path.name}  (rows={len(daily_df)})")
    return daily_df[cols_out]

def main():
    combined = []
    for y in YEARS:
        in_path  = BASE / IN_FMT.format(year=y)
        out_path = BASE / OUT_FMT.format(year=y)
        try:
            out_df = daily_agg_one_year(in_path, out_path)
            if SAVE_COMBINED and out_df is not None:
                out_df = out_df.copy()
                out_df["year"] = y
                combined.append(out_df)
        except Exception as e:
            print(f"ปี {y}: {e}")

    if SAVE_COMBINED and combined:
        all_df = pd.concat(combined, ignore_index=True)
        all_df.to_csv(BASE / OUT_COMBINED, index=False, encoding="utf-8-sig")
        print(f"รวมทุกปีแล้ว → {OUT_COMBINED}  (rows={len(all_df)})")

if __name__ == "__main__":
    main()

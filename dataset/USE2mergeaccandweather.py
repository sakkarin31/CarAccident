# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
from pathlib import Path

# CONFIG (หลายปี)
BASE = Path(".")
YEARS = [2020, 2021, 2022, 2023, 2024]  # ปรับปีตามต้องการ
WEATHER_FMT  = "songkhla_weather_{year}-final_3class.csv"
ACCIDENT_FMT = "accident{year}-4_songkhla.csv"
OUT_FMT      = "songkhla_weather_{year}-merged.csv"
SPECIALS_CSV = BASE / "special_days_th.csv"   # optional: date,name,kind

# HELPERS
def read_csv_smart(path: Path):
    last_err = None
    for enc in ("utf-8", "utf-8-sig", "cp874", "iso-8859-11"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as e:
            last_err = e
    raise last_err

def parse_ddmmyyyy(s):
    # ถ้าไฟล์บางชุดเป็นรูปอื่น ให้เพิ่ม fallback ได้ตามต้องการ
    return pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")

def parse_time_flexible(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    t1 = pd.to_datetime(s, format="%I:%M %p", errors="coerce")     # 12h
    m = t1.isna()
    if m.any():
        t2 = pd.to_datetime(s[m], format="%H:%M:%S", errors="coerce")
        t1.loc[m] = t2
    m = t1.isna()
    if m.any():
        t3 = pd.to_datetime(s[m], format="%H:%M", errors="coerce")
        t1.loc[m] = t3
    m = t1.isna()
    if m.any():
        def coerce_hour(x):
            x = str(x).strip()
            if x.isdigit():
                return pd.to_datetime(f"{int(x):02d}:00:00", format="%H:%M:%S", errors="coerce")
            return pd.NaT
        t4 = s[m].map(coerce_hour)
        t1.loc[m] = t4
    return t1

def fill_and_cast(df, count_cols):
    out = df.copy()
    out["LATITUDE"]  = out["LATITUDE"].astype(object)
    out["LONGITUDE"] = out["LONGITUDE"].astype(object)
    out["LATITUDE"]  = out["LATITUDE"].where(out["LATITUDE"].notna(), "-")
    out["LONGITUDE"] = out["LONGITUDE"].where(out["LONGITUDE"].notna(), "-")
    for c in count_cols:
        out[c] = out[c].fillna(0).astype(int).astype(str)
    return out

def load_specials(csv_path: Path):
    if csv_path.exists():
        sd = pd.read_csv(csv_path)
        sd["date"] = pd.to_datetime(sd["date"], errors="coerce").dt.date
        sd = sd.dropna(subset=["date"])
        name_map = dict(zip(sd["date"], sd["name"]))
        kind_series = sd.get("kind", pd.Series(["special"] * len(sd)))
        kind_map = dict(zip(sd["date"], kind_series))
        return name_map, kind_map
    return {}, {}

def process_year(year: int):
    WEATHER_FILE  = BASE / WEATHER_FMT.format(year=year)
    ACCIDENT_FILE = BASE / ACCIDENT_FMT.format(year=year)
    OUT_FILE      = BASE / OUT_FMT.format(year=year)

    if not WEATHER_FILE.exists():
        print(f"{year}: ไม่พบไฟล์สภาพอากาศ -> {WEATHER_FILE.name} (ข้ามปีนี้)")
        return
    if not ACCIDENT_FILE.exists():
        print(f"{year}: ไม่พบไฟล์อุบัติเหตุ -> {ACCIDENT_FILE.name} (ข้ามปีนี้)")
        return

    # LOAD
    dfw = read_csv_smart(WEATHER_FILE)
    dfa = read_csv_smart(ACCIDENT_FILE)

    # WEATHER KEY (30-min grid already)
    dtw_date = parse_ddmmyyyy(dfw["date"])
    dtw_time = pd.to_datetime(dfw["time"], format="%I:%M %p", errors="coerce")
    dtw = pd.to_datetime(dtw_date.dt.strftime("%Y-%m-%d")) \
        + pd.to_timedelta(dtw_time.dt.hour.fillna(0), unit="h") \
        + pd.to_timedelta(dtw_time.dt.minute.fillna(0), unit="m") \
        + pd.to_timedelta(dtw_time.dt.second.fillna(0), unit="s")
    dfw = dfw.copy()
    dfw["__dt30__"] = dtw

    # ACCIDENT KEY (round to 30min)
    dta_date = pd.to_datetime(dfa["วันที่เกิดเหตุ"], errors="coerce", dayfirst=True)
    dta_time = parse_time_flexible(dfa["เวลา"])
    dta = pd.to_datetime(dta_date.dt.strftime("%Y-%m-%d")) \
        + pd.to_timedelta(dta_time.dt.hour.fillna(0), unit="h") \
        + pd.to_timedelta(dta_time.dt.minute.fillna(0), unit="m") \
        + pd.to_timedelta(dta_time.dt.second.fillna(0), unit="s")
    dfa = dfa.copy()
    dfa["__dt30__"] = dta.dt.round("30min")

    # ensure columns exist
    ACC_COLS = ["LATITUDE", "LONGITUDE", "รถที่เกิดเหตุ", "น้อยกว่ารถ4ล้อ", "รถ4ล้อ", "มากกว่ารถ4ล้อ", "เกิดเหตุ"]
    for c in ACC_COLS:
        if c not in dfa.columns:
            dfa[c] = np.nan

    # cast numbers
    count_cols = ["รถที่เกิดเหตุ", "น้อยกว่ารถ4ล้อ", "รถ4ล้อ", "มากกว่ารถ4ล้อ", "เกิดเหตุ"]
    for c in count_cols:
        dfa[c] = pd.to_numeric(dfa[c], errors="coerce")
    dfa["LATITUDE"]  = pd.to_numeric(dfa["LATITUDE"], errors="coerce")
    dfa["LONGITUDE"] = pd.to_numeric(dfa["LONGITUDE"], errors="coerce")

    # aggregate per 30-min
    agg_dict = {c: "sum" for c in count_cols}
    agg_dict.update({"LATITUDE":"first","LONGITUDE":"first"})
    dfa_agg = dfa.groupby("__dt30__", as_index=True).agg(agg_dict)

    # merge
    merged = pd.merge(dfw, dfa_agg, how="left", left_on="__dt30__", right_index=True)

    # defaults / rename / placeholders
    merged = fill_and_cast(merged, count_cols)
    rename_map = {"น้อยกว่ารถ4ล้อ":"less4wheelacc", "รถ4ล้อ":"4wheelacc", "มากกว่ารถ4ล้อ":"more4wheelacc"}
    merged = merged.rename(columns=rename_map)
    for c in ["vehicles_lt_4_wheels","vehicles_4_wheels","vehicles_gt_4_wheels"]:
        if c not in merged.columns:
            merged[c] = ""

    # base columns ordering
    weather_cols = [c for c in dfw.columns if c != "__dt30__"]
    final_cols = weather_cols + [
        "LATITUDE","LONGITUDE","less4wheelacc","4wheelacc","more4wheelacc","เกิดเหตุ",
        "vehicles_lt_4_wheels","vehicles_4_wheels","vehicles_gt_4_wheels"
    ]

    # Day-of-week & Holidays (TH)
    dt = pd.to_datetime(merged["__dt30__"], errors="coerce")
    merged["day_of_week"] = dt.dt.dayofweek
    merged["is_weekend"]  = (merged["day_of_week"] >= 5).astype(int)

    years = sorted({d.year for d in dt.dropna()})
    try:
        import holidays
        th = holidays.TH(years=years, observed=True)
        merged["holiday_name"] = dt.dt.date.map(th)
        merged["is_holiday"]   = merged["holiday_name"].notna().astype(int)
    except Exception:
        merged["holiday_name"] = pd.NA
        merged["is_holiday"]   = 0

    special_name_map, special_kind_map = load_specials(SPECIALS_CSV)
    merged["special_name"]        = dt.dt.date.map(special_name_map)
    merged["special_kind"]        = dt.dt.date.map(special_kind_map)
    merged["is_special_holiday"]  = merged["special_name"].notna().astype(int)
    merged["is_any_holiday"]      = ((merged["is_holiday"]==1) | (merged["is_special_holiday"]==1)).astype(int)

    extra_cols = [
        "day_of_week","is_weekend","is_holiday","is_special_holiday","is_any_holiday",
        "holiday_name","special_name","special_kind"
    ]
    for c in extra_cols:
        if c not in final_cols and c in merged.columns:
            final_cols.append(c)

    # save
    merged[final_cols].to_csv(OUT_FILE, index=False, encoding="utf-8-sig")
    print(f"{year}: Saved -> {OUT_FILE.name}")

def main():
    for y in YEARS:
        try:
            process_year(y)
        except Exception as e:
            print(f"{y}: เกิดข้อผิดพลาด: {e}")

if __name__ == "__main__":
    main()

import pandas as pd
import numpy as np
from pathlib import Path
from typing import List, Optional

# CONFIG
YEARS = [2020, 2021, 2022, 2023, 2024]
IN_PATTERN = "songkhla_weather_{year}.csv"
ROUNDING = "round"
BASE_DIR = Path(".")
FORCE_FULL_YEAR = True
INTERP_LIMIT_STEPS: Optional[int] = 48


# HELPERS
def _find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand in cols_lower:
            return cols_lower[cand]
    return None


def _parse_date_series(s: pd.Series):
    s_clean = s.astype(str).str.strip().replace({"": np.nan, "nan": np.nan})
    dt1 = pd.to_datetime(s_clean, errors="coerce", dayfirst=False)
    dt2 = pd.to_datetime(s_clean, errors="coerce", dayfirst=True)
    return dt2 if dt2.isna().sum() < dt1.isna().sum() else dt1


def _parse_time_series(s: pd.Series):
    s_clean = s.astype(str).str.strip().str.upper().replace({"": np.nan, "NAN": np.nan})

    def norm_one(x: str):
        if pd.isna(x):
            return np.nan
        x = x.strip()
        if ":" not in x:
            try:
                h = int(x)
                return f"{h:02d}:00:00"
            except:
                return np.nan
        parts = x.split()
        time_part = parts[0]
        ampm = parts[1] if len(parts) > 1 and parts[1] in ("AM", "PM") else ""
        tparts = time_part.split(":")
        try:
            if len(tparts) == 1:
                h, m, s = int(tparts[0]), 0, 0
            elif len(tparts) == 2:
                h, m, s = int(tparts[0]), int(tparts[1]), 0
            else:
                h, m, s = int(tparts[0]), int(tparts[1]), int(tparts[2])
        except:
            return np.nan
        t24 = f"{h:02d}:{m:02d}:{s:02d}"
        return t24 + (f" {ampm}" if ampm else "")

    s_norm = s_clean.map(norm_one)
    parsed = pd.to_datetime(s_norm, errors="coerce")
    mask_nat = parsed.isna() & s_norm.notna()
    if mask_nat.any():
        parsed2 = pd.to_datetime(s_norm[mask_nat], errors="coerce", format="%H:%M:%S")
        parsed.loc[mask_nat] = parsed2
    return parsed


def map_condition_3class(series: pd.Series) -> pd.Series:
    """Map raw condition strings to one of {Clear, Rain, Mist}."""
    s = series.astype(str).str.lower().fillna("")

    def cat(x: str) -> str:
        x = x.strip()
        if any(k in x for k in ["rain", "shower", "storm", "drizzle", "thunder"]):
            return "Rain"
        if any(k in x for k in ["mist", "fog", "haze", "smoke"]):
            return "Mist"
        if x in ["", "nan", "none", "clear", "fair", "sunny"] or "partly cloudy" in x or "mostly clear" in x:
            return "Clear"
        return "Clear"

    return s.map(cat)


def _is_leap(y: int) -> bool:
    return (y % 4 == 0) and (y % 100 != 0 or y % 400 == 0)


# OUTLIER HANDLING
def clip_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """Clean unrealistic values and replace zeros with median before interpolation."""
    df = df.copy()

    # แทนค่า 0 ด้วย median
    for col in ["temperature_f", "humidity_pct", "pressure_in"]:
        if col in df.columns:
            median_val = df.loc[df[col] != 0, col].median(skipna=True)
            df[col] = np.where(df[col] == 0, median_val, df[col])

    # Clip ค่าผิดช่วงให้อยู่ในขอบเขตสมเหตุสมผล
    if "temperature_f" in df.columns:
        df["temperature_f"] = df["temperature_f"].clip(70, 100)
    if "humidity_pct" in df.columns:
        df["humidity_pct"] = df["humidity_pct"].clip(40, 100)
    if "pressure_in" in df.columns:
        df["pressure_in"] = df["pressure_in"].clip(28, 31)
    if "wind_speed_kmh" in df.columns:
        df["wind_speed_kmh"] = df["wind_speed_kmh"].clip(0, 100)

    return df


# CORE FUNCTION
def reformat_and_fill(df: pd.DataFrame,
                      rounding: str = "round",
                      force_full_year: bool = False,
                      force_year: Optional[int] = None,
                      interp_limit_steps: Optional[int] = None,
                      debug: bool = True):
    # หา column date/time
    date_col = _find_col(df, ["date", "วันที่", "วันเดือนปี"])
    time_col = _find_col(df, ["time", "เวลา"])
    if date_col is None or time_col is None:
        raise ValueError("ต้องมีคอลัมน์ 'date' และ 'time' ในไฟล์")

    # รวม date + time → datetime
    d = _parse_date_series(df[date_col])
    t = _parse_time_series(df[time_col])
    d_only = pd.to_datetime(d.dt.strftime("%Y-%m-%d"), errors="coerce")
    dt = d_only + pd.to_timedelta(t.dt.hour.fillna(0).astype(int), unit="h") \
               + pd.to_timedelta(t.dt.minute.fillna(0).astype(int), unit="m") \
               + pd.to_timedelta(t.dt.second.fillna(0).astype(int), unit="s")

    # ปัดเวลาให้ลงกริด 30 นาที
    if rounding == "floor":
        dt30 = dt.dt.floor("30min")
    elif rounding == "ceil":
        dt30 = dt.dt.ceil("30min")
    else:
        dt30 = dt.dt.round("30min")

    df = df.copy()
    df["__dt30__"] = dt30
    dropped = int(df["__dt30__"].isna().sum())
    if debug and dropped > 0:
        print(f"[DEBUG] rows with unparseable datetime (dropped): {dropped}")

    # Group และรวมค่าใน bin เวลาเดียวกัน
    features = [c for c in df.columns if c not in [date_col, time_col, "__dt30__"]]
    numeric_like = [c for c in features if pd.to_numeric(df[c], errors="coerce").notna().sum() > 0]
    non_numeric = [c for c in features if c not in numeric_like]
    agg = {**{c: "mean" for c in numeric_like}, **{c: "first" for c in non_numeric}}
    g = df.groupby("__dt30__", as_index=True).agg(agg)

    # Reindex ให้ครบปีหรือช่วงจริง
    if force_full_year and force_year:
        start = pd.Timestamp(f"{force_year}-01-01 00:00:00")
        end = pd.Timestamp(f"{force_year}-12-31 23:30:00")
        full = pd.date_range(start, end, freq="30min")
        reidx = g.reindex(full)
    else:
        start = g.index.min().floor("D")
        end = g.index.max().ceil("D") - pd.Timedelta(minutes=30)
        full = pd.date_range(start, end, freq="30min")
        reidx = g.reindex(full)

    before = reidx.copy()

    # 🧹 CLEAN OUTLIERS ก่อนเติมค่า
    reidx = clip_outliers(reidx)

    # เติมค่าตัวเลข (rolling + interpolate)
    num_cols = [c for c in reidx.columns if pd.to_numeric(reidx[c], errors="coerce").notna().sum() > 0]
    if num_cols:
        num_df = reidx[num_cols].apply(pd.to_numeric, errors="coerce")
        num_df = num_df.rolling(window=11, center=True, min_periods=1).mean()
        if interp_limit_steps is not None:
            num_df = num_df.interpolate(method="time", limit=interp_limit_steps, limit_direction="both")
        else:
            num_df = num_df.interpolate(method="time", limit_direction="both")
        num_df = num_df.ffill().bfill()
        reidx[num_cols] = num_df

    # Condition mapping
    cond_col = next((c for c in ["condition", "สภาพอากาศ", "weather"]
                     if c in reidx.columns and c not in num_cols), None)
    if cond_col:
        filled_cond = before[cond_col].astype(object).ffill().bfill().fillna("Clear")
        reidx[cond_col] = map_condition_3class(filled_cond)

    # ใส่ date/time + ปัดทศนิยม
    out = reidx.copy()
    out.insert(0, "date", out.index.strftime("%d/%m/%Y"))
    out.insert(1, "time", out.index.strftime("%I:%M %p"))
    out = out[["date", "time"] + [c for c in reidx.columns]]
    for c in out.select_dtypes(include=[float]).columns:
        out[c] = out[c].round(2)

    if debug:
        print(f"[DEBUG] reindexed range: {full[0]} .. {full[-1]} | rows={len(full)}")

    return before, out


# MAIN PROCESS
def process_year(year: int,
                 base_dir: Path = BASE_DIR,
                 rounding: str = ROUNDING,
                 force_full_year: bool = FORCE_FULL_YEAR,
                 interp_limit_steps: Optional[int] = INTERP_LIMIT_STEPS):
    in_path = base_dir / IN_PATTERN.format(year=year)
    if not in_path.exists():
        print(f"[SKIP] {in_path.name} not found.")
        return None

    print(f"[PROCESS] {in_path.name}")
    df = pd.read_csv(in_path, encoding="utf-8")

    before, after = reformat_and_fill(
        df,
        rounding=rounding,
        force_full_year=force_full_year,
        force_year=year,
        interp_limit_steps=interp_limit_steps,
        debug=True
    )

    out3 = base_dir / f"songkhla_weather_{year}-final_3class.csv"
    after.to_csv(out3, index=False, float_format="%.2f", encoding="utf-8")

    days_in_year = 366 if _is_leap(year) else 365
    should_rows = days_in_year * 48
    print(f"[INFO] Year {year}: expected rows={should_rows} (full-year), got={len(after)}")
    print(f"✅ Cleaned + Saved → {out3}\n")

    return out3


if __name__ == "__main__":
    for y in YEARS:
        process_year(y)

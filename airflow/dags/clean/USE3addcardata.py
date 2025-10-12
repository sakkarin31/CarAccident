import pandas as pd
import numpy as np
from pathlib import Path
from datetime import timedelta

# CONFIG (หลายปี)
BASE = Path(".")
YEARS = [2020, 2021, 2022, 2023, 2024]

WEATHER_FMT   = "songkhla_weather_{year}-merged.csv"
CARCOUNT_FMT  = "car-count67_{year}.csv"     # จะใช้ถ้ามี; ไม่มีก็ไปใช้ CARCOUNT_FALLBACK
CARCOUNT_FALLBACK = "car-count67.csv"
SPECIALS_CSV  = BASE / "special_days_th.csv" # optional: date,name,kind
OUT_FMT       = "datafinal-{year}-perday.csv"

# block shares (รวมทั้งวัน=1)
BLOCK_FACTORS_SMALL = {"late_night":0.05, "morning":0.25, "mid_day":0.30, "evening":0.25, "night":0.15}
BLOCK_FACTORS_FOUR  = {"late_night":0.10, "morning":0.35, "mid_day":0.30, "evening":0.35, "night":0.20}
BLOCK_FACTORS_HEAVY = {"late_night":0.25, "morning":0.15, "mid_day":0.25, "evening":0.20, "night":0.15}

# ตัวคูณอากาศต่อแถว
WEATHER_MULT = {
    "Clear": {"small":1.00, "four":1.00, "heavy":1.00},
    "Mist":  {"small":0.90, "four":1.00, "heavy":1.00},
    "Rainy": {"small":0.70, "four":1.10, "heavy":1.00},
}

# ตัวคูณระดับ "วัน"
M_SMALL_DAY = {"weekday":1.00, "weekend":1.08, "holiday":1.15, "special":1.25}
M_FOUR_DAY  = {"weekday":1.00, "weekend":1.25, "holiday":1.45, "special":1.65}
M_HEAVY_DAY = {"weekday":1.00, "weekend":0.95, "holiday":0.85, "special":0.75}

# ปิดอีฟ/วันกลับแบบเดิม (ใช้ ramp หยุดยาวแทน)
EVE_RETURN_SMALL = 1.00
EVE_RETURN_FOUR  = 1.00
EVE_RETURN_HEAVY = 1.00

# ===== Long-break ramp config =====
LONG_BREAK_MIN = 4
PRE_RAMP_SMALL = {1: 1.12, 2: 1.06, 3: 1.03}
PRE_RAMP_FOUR  = {1: 1.40, 2: 1.22, 3: 1.10}
PRE_RAMP_HEAVY = {1: 0.95, 2: 0.97, 3: 0.99}
FIRST_DAY_BOOST = {"small": 1.08, "four": 1.20, "heavy": 0.90}
POST_RAMP_SMALL = {1: 1.08, 2: 1.04}
POST_RAMP_FOUR  = {1: 1.28, 2: 1.12}
POST_RAMP_HEAVY = {1: 0.92, 2: 0.96}

MIN_WEEKDAY_SHARE = None  # เช่น 0.45 ถ้าต้องการ floor ทั้งชุด
RNG = np.random.default_rng(2024)
JITTER_WITHIN_DAY = 0.20

# Helpers
def time_block(h):
    if 0 <= h <= 4:   return "late_night"
    if 5 <= h <= 8:   return "morning"
    if 9 <= h <= 15:  return "mid_day"
    if 16 <= h <= 19: return "evening"
    return "night"

def normalize_condition(c):
    c = str(c).strip().lower()
    if "rain" in c or "shower" in c or "storm" in c:
        return "Rainy"
    if "mist" in c or "fog" in c:
        return "Mist"
    return "Clear"

def allocate_from_weights(weights, total):
    w = np.asarray(weights, float)
    s = w.sum()
    if s <= 0 or total <= 0:
        return np.zeros(len(w), dtype=int)
    w = w / s
    raw = w * int(total)
    base = np.floor(raw).astype(int)
    remainder = int(total) - base.sum()
    if remainder > 0:
        idx = np.argsort(-(raw - base))[:remainder]
        base[idx] += 1
    return base

def read_car_totals(path: Path):
    car = pd.read_csv(path)
    def coerce_numeric(x):
        try: return float(x)
        except: return np.nan
    car["_สาย_numeric_"] = car["ทางหลวงสาย"].apply(coerce_numeric)
    if car["_สาย_numeric_"].isna().any():   # แถว "รวม"
        total_row = car.loc[car["_สาย_numeric_"].isna()].iloc[-1]
        g_small = int(total_row["vehicles_lt_4_wheels"])
        g_four  = int(total_row["vehicles_4_wheels"])
        g_heavy = int(total_row["vehicles_gt_4_wheels"])
    else:
        subset = car[car["_สาย_numeric_"] == 4]
        g_small = int(subset["vehicles_lt_4_wheels"].sum())
        g_four  = int(subset["vehicles_4_wheels"].sum())
        g_heavy = int(subset["vehicles_gt_4_wheels"].sum())
    return g_small, g_four, g_heavy

def compute_day_totals(days, M_day, eve_return_factor, grand_total,
                       min_weekday_share=None, day_noise_sd=0.12,
                       dow_profile=None, month_profile=None, rng=None,
                       long_break_min=4, pre_ramp=None, post_ramp=None, first_day_boost=1.0):
    if rng is None:
        rng = np.random.default_rng(2024)
    base = days["kind"].map(M_day).astype(float).values
    base *= np.where(days["is_eve_or_return"].values == 1, eve_return_factor, 1.0)

    if dow_profile is not None:
        f = days["day_of_week"].map(lambda d: dow_profile.get(int(d), 1.0)).astype(float).values
        base *= f
    if month_profile is not None:
        months = pd.to_datetime(days["date_only"]).dt.month.values
        f = np.array([month_profile.get(int(m), 1.0) for m in months], float)
        base *= f

    # หา segment หยุดยาว (non-working ≥ long_break_min)
    nonwork = (days["is_weekend"].astype(bool) |
               days["is_holiday"].astype(bool) |
               days["is_special"].astype(bool)).to_numpy()
    n = len(days)
    i = 0
    while i < n:
        if nonwork[i]:
            j = i
            while j + 1 < n and nonwork[j + 1]:
                j += 1
            seg_len = j - i + 1
            if seg_len >= long_break_min:
                base[i] *= float(first_day_boost)
                if pre_ramp:
                    for k in sorted(pre_ramp):
                        t = i - k
                        if t >= 0:
                            base[t] *= float(pre_ramp[k])
                if post_ramp:
                    for k in sorted(post_ramp):
                        t = j + k
                        if t < n:
                            base[t] *= float(post_ramp[k])
            i = j + 1
        else:
            i += 1

    if min_weekday_share is not None and (days["kind"]=="weekday").any():
        wk = (days["kind"]=="weekday").values
        other = ~wk
        S = base.sum()
        if S > 0:
            share_wk = base[wk].sum() / S
            if share_wk < min_weekday_share and base[other].sum() > 0:
                need = min_weekday_share * S - base[wk].sum()
                scale = (base[other].sum() - need) / base[other].sum()
                scale = max(scale, 0.0)
                base[other] *= scale

    if day_noise_sd and day_noise_sd > 0:
        noise = np.exp(rng.normal(0, day_noise_sd, size=base.shape))
        base = base * noise
        sm = pd.Series(base).rolling(3, min_periods=1, center=True).mean().to_numpy()
        alpha = 0.6
        base = alpha*sm + (1-alpha)*base

    return allocate_from_weights(base, grand_total)

def block_weight_day(df_day, block_table, veh_weather_key):
    hours = df_day["hour"].to_numpy()
    blocks = np.array([time_block(h) for h in hours], dtype=object)
    uniq, cnt = np.unique(blocks, return_counts=True)
    per_len = {b:c for b,c in zip(uniq, cnt)}
    w = np.array([block_table[b]/per_len[b] for b in blocks], dtype=float)
    conds = df_day["cond_norm"].to_numpy()
    mults = np.array([WEATHER_MULT.get(c, WEATHER_MULT["Clear"])[veh_weather_key] for c in conds], float)
    w *= mults
    if JITTER_WITHIN_DAY and JITTER_WITHIN_DAY > 0:
        jitter = RNG.normal(0, JITTER_WITHIN_DAY, size=w.shape)
        w = np.clip(w*(1+jitter), 1e-12, None)
    return w

# Pipeline: 1 ปี
def run_one_year(year: int):
    weather_path  = BASE / WEATHER_FMT.format(year=year)
    carcount_path = BASE / CARCOUNT_FMT.format(year=year)
    if not carcount_path.exists():
        carcount_path = BASE / CARCOUNT_FALLBACK
    out_path      = BASE / OUT_FMT.format(year=year)

    if not weather_path.exists():
        print(f"{year}: ไม่พบ {weather_path.name} — ข้าม")
        return

    # Load weather merged
    df = pd.read_csv(weather_path)
    dt_str = df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip()
    try:
        df["datetime"] = pd.to_datetime(dt_str, format="%m/%d/%Y %I:%M %p", errors="raise")
    except Exception:
        df["datetime"] = pd.to_datetime(dt_str, errors="coerce")
    if df["datetime"].isna().any():
        raise ValueError(f"{year}: แปลง datetime ไม่ได้บางแถว")

    df["hour"] = df["datetime"].dt.hour
    df["cond_norm"] = df["condition"].map(normalize_condition)
    df["date_only"] = df["datetime"].dt.date
    df["day_of_week"] = df["datetime"].dt.dayofweek
    df["is_weekend"]  = (df["day_of_week"] >= 5).astype(int)

    # Holidays & specials
    years = sorted(set(df["datetime"].dt.year))
    try:
        import holidays
        th = holidays.TH(years=years, observed=True)
        df["is_holiday"] = df["date_only"].map(lambda d: int(d in th))
    except Exception:
        th = {}
        df["is_holiday"] = 0

    special_set = set()
    p = SPECIALS_CSV
    if p.exists():
        sp = pd.read_csv(p)
        sp["date"] = pd.to_datetime(sp["date"], errors="coerce").dt.date
        special_set = set(sp["date"].dropna())
    df["is_special"] = df["date_only"].isin(special_set).astype(int)

    base_like = set(getattr(th, "keys", lambda: [])()) | special_set
    eve_set = {d - timedelta(days=1) for d in base_like}
    ret_set = {d + timedelta(days=1) for d in base_like}
    df["is_eve_or_return"] = df["date_only"].isin(eve_set | ret_set).astype(int)

    days = (
        df.groupby("date_only", as_index=False)
          .agg(day_of_week=("day_of_week","first"),
               is_weekend=("is_weekend","first"),
               is_holiday=("is_holiday","first"),
               is_special=("is_special","first"),
               is_eve_or_return=("is_eve_or_return","first"))
          .sort_values("date_only")
    )
    def day_kind(r):
        if r.is_special: return "special"
        if r.is_holiday: return "holiday"
        if r.is_weekend: return "weekend"
        return "weekday"
    days["kind"] = days.apply(day_kind, axis=1)

    # GRAND totals
    G_SMALL, G_FOUR, G_HEAVY = read_car_totals(carcount_path)

    # Step A: day totals (มี noise + long-break ramps)
    day_total_small = compute_day_totals(
        days, M_SMALL_DAY, EVE_RETURN_SMALL, G_SMALL,
        min_weekday_share=MIN_WEEKDAY_SHARE, day_noise_sd=0.12, rng=RNG,
        long_break_min=LONG_BREAK_MIN,
        pre_ramp=PRE_RAMP_SMALL, post_ramp=POST_RAMP_SMALL,
        first_day_boost=FIRST_DAY_BOOST["small"]
    )
    day_total_four = compute_day_totals(
        days, M_FOUR_DAY, EVE_RETURN_FOUR, G_FOUR,
        min_weekday_share=MIN_WEEKDAY_SHARE, day_noise_sd=0.15, rng=RNG,
        long_break_min=LONG_BREAK_MIN,
        pre_ramp=PRE_RAMP_FOUR, post_ramp=POST_RAMP_FOUR,
        first_day_boost=FIRST_DAY_BOOST["four"]
    )
    day_total_heavy = compute_day_totals(
        days, M_HEAVY_DAY, EVE_RETURN_HEAVY, G_HEAVY,
        min_weekday_share=MIN_WEEKDAY_SHARE, day_noise_sd=0.08, rng=RNG,
        long_break_min=LONG_BREAK_MIN,
        pre_ramp=PRE_RAMP_HEAVY, post_ramp=POST_RAMP_HEAVY,
        first_day_boost=FIRST_DAY_BOOST["heavy"]
    )

    day_total_small = pd.Series(day_total_small, index=days["date_only"]).astype(int)
    day_total_four  = pd.Series(day_total_four,  index=days["date_only"]).astype(int)
    day_total_heavy = pd.Series(day_total_heavy, index=days["date_only"]).astype(int)

    # Step B: allocate within-day to rows
    df_out = df.copy()
    for d0, grp in df.groupby("date_only", sort=True):
        idx = grp.index
        w = block_weight_day(grp, BLOCK_FACTORS_SMALL, "small")
        df_out.loc[idx, "vehicles_lt_4_wheels"] = allocate_from_weights(w, int(day_total_small.loc[d0]))
        w = block_weight_day(grp, BLOCK_FACTORS_FOUR, "four")
        df_out.loc[idx, "vehicles_4_wheels"]     = allocate_from_weights(w, int(day_total_four.loc[d0]))
        w = block_weight_day(grp, BLOCK_FACTORS_HEAVY, "heavy")
        df_out.loc[idx, "vehicles_gt_4_wheels"]  = allocate_from_weights(w, int(day_total_heavy.loc[d0]))

    # เติมคอลัมน์อุบัติเหตุที่อาจขาด
    for c in ["เกิดเหตุ","รถน้อยกว่า4ล้อacc","รถ4ล้อacc","รถมากกว่า4ล้อacc"]:
        if c not in df_out.columns:
            df_out[c] = 0

    # columns & save
    cols_out = [
        "date","time","temperature_F","humidity_%","pressure_in","condition",
        "เกิดเหตุ","รถน้อยกว่า4ล้อacc","รถ4ล้อacc","รถมากกว่า4ล้อacc",
        "vehicles_lt_4_wheels","vehicles_4_wheels","vehicles_gt_4_wheels",
        "day_of_week","is_weekend","is_holiday","is_special","is_eve_or_return"
    ]
    cols_out = [c for c in cols_out if c in df_out.columns]
    df_out[cols_out].to_csv(out_path, index=False, encoding="utf-8-sig")

    # sanity check
    assert df_out["vehicles_lt_4_wheels"].sum() == G_SMALL
    assert df_out["vehicles_4_wheels"].sum()    == G_FOUR
    assert df_out["vehicles_gt_4_wheels"].sum() == G_HEAVY

    print(f"{year}: Saved -> {out_path.name}")

# Run all years
if __name__ == "__main__":
    for y in YEARS:
        try:
            run_one_year(y)
        except Exception as e:
            print(f"{y}: {e}")

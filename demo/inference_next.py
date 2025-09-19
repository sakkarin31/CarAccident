# inference_next.py
# -*- coding: utf-8 -*-
import json, joblib, numpy as np, pandas as pd, torch
import torch.nn as nn
from datetime import datetime

# ----- ค่าต้องตรงกับตอนเทรน -----
SEQ_LENGTH = 24  # ถ้าตอนเทรนใช้ค่าที่ต่างออกไป ให้แก้ให้ตรง

MODEL_PATH = "model/lstm_best_model_mt.pth"
SCALER_X_PATH = "model/scaler_X.pkl"
SCALER_YREG_PATH = "model/scaler_yreg.pkl"
CALIBRATOR_PATH = "model/isotonic_calibrator.pkl"   # optional
THRESHOLD_PATH = "model/threshold.json"             # optional
FEATURE_COLS_PATH = "model/feature_cols.json"       # strongly recommended

# ----- โหลด feature_cols ที่บันทึกจากตอนเทรน -----
with open(FEATURE_COLS_PATH, "r", encoding="utf-8") as f:
    FEATURE_COLS = json.load(f)

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# ----- โมเดลต้องนิยามเหมือนตอนเทรน -----
class LSTM_MT(nn.Module):
    def __init__(self, input_size, hidden=128, layers=2, dropout=0.2, out_reg=3):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden, layers, batch_first=True, dropout=dropout)
        self.head_cls = nn.Linear(hidden, 1)
        self.head_reg = nn.Linear(hidden, out_reg)
    def forward(self, x):
        h, _ = self.lstm(x)
        h = h[:, -1, :]
        return self.head_cls(h), self.head_reg(h)

# ----- โหลด weights + scalers + calibrator/threshold -----
scaler_X    = joblib.load(SCALER_X_PATH)
scaler_yreg = joblib.load(SCALER_YREG_PATH)

try:
    calibrator = joblib.load(CALIBRATOR_PATH)
except:
    calibrator = None

try:
    import json
    with open(THRESHOLD_PATH, "r") as f:
        THRESH = float(json.load(f).get("threshold", 0.5))
except:
    THRESH = 0.5

model = LSTM_MT(input_size=len(FEATURE_COLS), hidden=128, layers=2, dropout=0.2, out_reg=3).to(device)
state = torch.load(MODEL_PATH, map_location=device)
model.load_state_dict(state); model.eval()

# ---------- Utilities ----------
def _ensure_datetime_cols(df: pd.DataFrame) -> pd.DataFrame:
    """พยายามสร้างคอลัมน์เวลา hour/day_of_week/month จาก datetime หรือจาก date+time"""
    if "datetime" in df.columns:
        dt = pd.to_datetime(df["datetime"], errors="coerce")
    else:
        if "date" in df.columns and "time" in df.columns:
            dt = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str),
                                dayfirst=True, format="mixed", errors="coerce")
        else:
            dt = pd.to_datetime(df.index, errors="coerce")
    df = df.copy()
    df["datetime"] = dt
    df = df.sort_values("datetime")
    df["hour"] = df["datetime"].dt.hour
    df["day_of_week"] = df["datetime"].dt.dayofweek
    df["month"] = df["datetime"].dt.month
    return df

def _normalize_daynight(df: pd.DataFrame) -> pd.DataFrame:
    """แปลง กลางวันกลางคืน เป็น 0/1 ถ้ายังไม่ใช่ตัวเลข"""
    if "กลางวันกลางคืน" in df.columns:
        if not np.issubdtype(df["กลางวันกลางคืน"].dtype, np.number):
            df["กลางวันกลางคืน"] = df["กลางวันกลางคืน"].map({"กลางวัน":1, "กลางคืน":0}).fillna(0)
    return df

def _one_hot_condition_align(df: pd.DataFrame, feature_cols_ref: list) -> pd.DataFrame:
    """ทำ one-hot 'condition' แล้วจัดคอลัมน์ cond_* ให้ตรงกับ FEATURE_COLS จากตอนเทรน"""
    df = df.copy()
    cond_cols_ref = [c for c in feature_cols_ref if str(c).startswith("cond_")]
    # ถ้ามีคอลัมน์ condition ดิบ → one-hot ใหม่
    if "condition" in df.columns:
        cond_dum = pd.get_dummies(df["condition"], prefix="cond")
        df = pd.concat([df.drop(columns=["condition"]), cond_dum], axis=1)
    # เติม cond_* ที่หายไปให้เป็น 0
    for c in cond_cols_ref:
        if c not in df.columns:
            df[c] = 0.0
    # ตัด cond_* ที่มีเกินจากตอนเทรน (กันผิดลำดับ)
    for c in [c for c in df.columns if str(c).startswith("cond_") and c not in cond_cols_ref]:
        df.drop(columns=[c], inplace=True)
    return df

def prepare_features(df_raw: pd.DataFrame) -> pd.DataFrame:
    """จัด df ที่อัปโหลดให้มีฟีเจอร์ตาม FEATURE_COLS และเรียงคอลัมน์ให้ตรง"""
    df = df_raw.copy()
    df = _ensure_datetime_cols(df)
    df = _normalize_daynight(df)
    df = _one_hot_condition_align(df, FEATURE_COLS)

    # สุดท้าย: เติมคอลัมน์ที่ยังขาดใน FEATURE_COLS ให้เป็น 0 และเรียงคอลัมน์
    for col in FEATURE_COLS:
        if col not in df.columns:
            df[col] = 0.0
    # บางคอลัมน์ที่อัปโหลดมาเกินก็อนุญาตได้ แต่เราจะหยิบเฉพาะ FEATURE_COLS เท่านั้น
    return df

def preprocess_window(df_window: pd.DataFrame) -> torch.Tensor:
    """รับ df 1 หน้าต่าง (เรียงเก่า->ใหม่) แล้วแปลงเป็นเทนเซอร์ (1, T, nfeat)"""
    X = df_window[FEATURE_COLS].astype("float32").values
    Xs = scaler_X.transform(X)
    return torch.tensor(Xs, dtype=torch.float32).unsqueeze(0)

@torch.no_grad()
def predict_next(df_window: pd.DataFrame):
    """
    input: df_window = แถวล่าสุด SEQ_LENGTH แถว (เรียงเวลาเก่า->ใหม่) หลัง prepare_features()
    return: prob, flag, reg_counts(int[3])
    """
    x = preprocess_window(df_window).to(device)
    logit, reg_scaled = model(x)

    prob = torch.sigmoid(logit).item()
    if calibrator is not None:
        prob = float(calibrator.predict([prob])[0])  # isotonic

    flag = int(prob >= THRESH)

    reg_inv = scaler_yreg.inverse_transform(reg_scaled.cpu().numpy())[0]
    reg_inv = np.clip(reg_inv, 0, None)
    reg_counts = np.round(reg_inv).astype(int)
    return prob, flag, reg_counts

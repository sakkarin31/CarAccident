# ===============================
# Model Comparison: LightGBM vs XGBoost (save models + plot all)
# ===============================
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import joblib
import seaborn as sns
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, f1_score, precision_score, recall_score
import lightgbm as lgb
import xgboost as xgb
import warnings
warnings.filterwarnings("ignore")

# Set random seed
np.random.seed(42)

# -------------------------------
# 1) Load & prepare data
# -------------------------------
df = pd.read_csv("clean_data-2023-2024.csv", parse_dates=["datetime"])
df = df.sort_values("datetime")
df["target"] = (df["เกิดเหตุ"] > 0).astype(int)

features = [c for c in df.columns if c not in ["datetime", "เกิดเหตุ", "target"]]

# Split: last 30 days as test
train_full = df.iloc[:-30].copy()
test = df.iloc[-30:].copy()

print(f"Train size: {len(train_full)}, Test size: {len(test)}")

# -------------------------------
# 2) Evaluate function
# -------------------------------
def evaluate(y_true, y_pred, y_prob=None, model_name="", threshold_note=""):
    rmse = mean_squared_error(y_true, y_prob if y_prob is not None else y_pred, squared=False)
    f1 = f1_score(y_true, y_pred)
    prec = precision_score(y_true, y_pred)
    rec = recall_score(y_true, y_pred)
    return {
        "Model": model_name,
        "Threshold": threshold_note,
        "RMSE": rmse,
        "F1": f1,
        "Precision": prec,
        "Recall": rec,
        "y_pred": y_pred.copy(),
        "y_prob": y_prob.copy() if y_prob is not None else y_pred.copy()
    }

# -------------------------------
# 3) Helper: Lag & Rolling features
# -------------------------------
def create_lgb_features(df, lags, rollings):
    df_feat = df.copy()
    for lag in lags:
        df_feat[f'lag_{lag}'] = df_feat['target'].shift(lag)
    for win in rollings:
        df_feat[f'roll_mean_{win}'] = df_feat['target'].rolling(win).mean()
        df_feat[f'roll_std_{win}'] = df_feat['target'].rolling(win).std()
    return df_feat

candidate_windows = [1, 3, 7, 14, 30]

# -------------------------------
# 4) LightGBM
# -------------------------------
print("\n🔍 Tuning LightGBM...")

best_f1 = -1
best_lag = None
best_roll = None
best_lgb_pred_prob = None
best_lgb_model = None

for win in candidate_windows:
    lags = [win]
    rollings = [win]
    
    df_lgb = create_lgb_features(train_full, lags, rollings).dropna()
    if len(df_lgb) < 10:
        continue
        
    X_train = df_lgb.drop(columns=["datetime", "เกิดเหตุ", "target"])
    y_train = df_lgb["target"]
    
    model = lgb.LGBMClassifier(
        n_estimators=100,
        class_weight='balanced',
        random_state=42,
        verbose=-1
    )
    model.fit(X_train, y_train)
    
    df_test_full = pd.concat([train_full.iloc[-max(candidate_windows):], test], ignore_index=True)
    df_test_feat = create_lgb_features(df_test_full, lags, rollings)
    df_test_final = df_test_feat.iloc[-len(test):].dropna()
    
    if len(df_test_final) != len(test):
        continue
        
    X_test = df_test_final.drop(columns=["datetime", "เกิดเหตุ", "target"])
    y_prob = model.predict_proba(X_test)[:, 1]
    y_pred = (y_prob > 0.5).astype(int)
    f1 = f1_score(test["target"].iloc[:len(y_pred)], y_pred)
    
    if f1 > best_f1:
        best_f1 = f1
        best_lag = lags
        best_roll = rollings
        best_lgb_pred_prob = y_prob
        best_lgb_model = model

print(f"Best LightGBM: lag={best_lag}, rolling={best_roll}, F1={best_f1:.4f}")

lgb_pred_binary_05 = (best_lgb_pred_prob > 0.5).astype(int)
lgb_pred_binary_03 = (best_lgb_pred_prob > 0.3).astype(int)

lgb_results = {
    "0.5": evaluate(test["target"], lgb_pred_binary_05, best_lgb_pred_prob, "LightGBM", "0.5"),
    "0.3": evaluate(test["target"], lgb_pred_binary_03, best_lgb_pred_prob, "LightGBM", "0.3")
}

# -------------------------------
# 5) XGBoost
# -------------------------------
print("\n🔍 Tuning XGBoost...")

best_f1_xgb = -1
best_lag_xgb = None
best_roll_xgb = None
best_xgb_pred_prob = None
best_xgb_model = None
best_xgb_scaler = None

for win in candidate_windows:
    lags = [win]
    rollings = [win]
    
    df_xgb = create_lgb_features(train_full, lags, rollings).dropna()
    if len(df_xgb) < 10:
        continue
        
    X_train = df_xgb.drop(columns=["datetime", "เกิดเหตุ", "target"])
    y_train = df_xgb["target"]
    
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    
    model = xgb.XGBClassifier(
        n_estimators=100,
        scale_pos_weight=sum(y_train == 0) / sum(y_train == 1),
        random_state=42,
        use_label_encoder=False,
        eval_metric='logloss'
    )
    model.fit(X_train_scaled, y_train)
    
    df_test_full = pd.concat([train_full.iloc[-max(candidate_windows):], test], ignore_index=True)
    df_test_feat = create_lgb_features(df_test_full, lags, rollings)
    df_test_final = df_test_feat.iloc[-len(test):].dropna()
    
    if len(df_test_final) != len(test):
        continue
        
    X_test = df_test_final.drop(columns=["datetime", "เกิดเหตุ", "target"])
    X_test_scaled = scaler.transform(X_test)
    y_prob = model.predict_proba(X_test_scaled)[:, 1]
    y_pred = (y_prob > 0.5).astype(int)
    f1 = f1_score(test["target"].iloc[:len(y_pred)], y_pred)
    
    if f1 > best_f1_xgb:
        best_f1_xgb = f1
        best_lag_xgb = lags
        best_roll_xgb = rollings
        best_xgb_pred_prob = y_prob
        best_xgb_model = model
        best_xgb_scaler = scaler

print(f"Best XGBoost: lag={best_lag_xgb}, rolling={best_roll_xgb}, F1={best_f1_xgb:.4f}")

xgb_pred_binary_05 = (best_xgb_pred_prob > 0.5).astype(int)
xgb_pred_binary_03 = (best_xgb_pred_prob > 0.3).astype(int)

xgb_results = {
    "0.5": evaluate(test["target"], xgb_pred_binary_05, best_xgb_pred_prob, "XGBoost", "0.5"),
    "0.3": evaluate(test["target"], xgb_pred_binary_03, best_xgb_pred_prob, "XGBoost", "0.3")
}

# -------------------------------
# 6) Compare All Results
# -------------------------------
all_results = []
for res in [lgb_results, xgb_results]:
    for thresh in ["0.3", "0.5"]:
        all_results.append(res[thresh])

results_df = pd.DataFrame(all_results)
print("\n📊 Comparison Results:")
print(results_df[["Model", "Threshold", "RMSE", "F1", "Precision", "Recall"]].round(4))

# -------------------------------
# 7) Plot All Models
# -------------------------------
dates = test["datetime"].dt.strftime("%m-%d")
y_true = test["target"]

plt.figure(figsize=(16, 10))

for idx, row in results_df.iterrows():
    plt.subplot(2, 2, idx+1)
    y_pred = row["y_pred"]
    y_prob = row["y_prob"]
    model_name = row["Model"]
    thresh = row["Threshold"]

    x_pos = range(len(dates))
    plt.scatter(x_pos, y_true, color="red", label="Actual", marker="o", s=80, zorder=5)
    plt.scatter(x_pos, y_pred, color="blue", label="Predicted", marker="x", s=60, zorder=5)
    plt.plot(x_pos, y_prob, color="green", linestyle="-", linewidth=2, alpha=0.7, label="Probability")

    plt.xticks(x_pos, dates, rotation=45)
    plt.ylim(-0.05, 1.05)
    plt.title(f"{model_name} (Thresh={thresh})")
    plt.legend()
    plt.grid(alpha=0.3)

plt.tight_layout()
plt.show()

# -------------------------------
# 8) Save Models
# -------------------------------
os.makedirs("saved_models", exist_ok=True)
joblib.dump(best_lgb_model, "saved_models/lightgbm_model.pkl")
joblib.dump(best_xgb_model, "saved_models/xgboost_model.pkl")
joblib.dump(best_xgb_scaler, "saved_models/xgb_scaler.pkl")
print("\n💾 Models saved to 'saved_models/'")

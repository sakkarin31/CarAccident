# -------------------------------------------------
# 1. โหลดไลบรารี
# -------------------------------------------------
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error, mean_absolute_percentage_error
from math import sqrt
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
import joblib
import os

# สร้างโฟลเดอร์ outputs
os.makedirs('outputs', exist_ok=True)

# -------------------------------------------------
# 2. โหลดข้อมูล
# -------------------------------------------------
df = pd.read_csv(r'C:\6610110050\CarAccident\dataset\cleandaily-all-years.csv')
df['datetime'] = pd.to_datetime(df['datetime'])
df = df.sort_values('datetime').reset_index(drop=True)

# กำหนดฟีเจอร์และ target (ทั้ง 4 ตัว)
features = ['temperature_F', 'humidity_%', 'wind_speed_kmh', 'pressure_in']
targets = ['temperature_F', 'humidity_%', 'pressure_in']

print("📅 ช่วงเวลาข้อมูล:")
print(f"   ปี: {df['year'].min()} – {df['year'].max()}")
print(f"   จำนวนวันทั้งหมด: {len(df)}")

# -------------------------------------------------
# 3. แบ่ง train/test โดยใช้ 30 วันสุดท้ายเป็น test
# -------------------------------------------------
test_days = 30

# แยกข้อมูลตามวันที่ (เรียงแล้วตั้งแต่ต้น)
df = df.sort_values('datetime').reset_index(drop=True)

# ใช้ข้อมูลทั้งหมด ยกเว้น 30 วันสุดท้ายเป็น train
df_train = df.iloc[:-test_days].copy()
df_test = df.iloc[-test_days:].copy()

print(f"\n📊 แบ่งข้อมูล:")
print(f"   Train: {len(df_train)} วัน (ไม่รวม {test_days} วันสุดท้าย)")
print(f"   Test:  {len(df_test)} วันล่าสุด")
print(f"   ช่วง train: {df_train['datetime'].min().date()} → {df_train['datetime'].max().date()}")
print(f"   ช่วง test:  {df_test['datetime'].min().date()} → {df_test['datetime'].max().date()}")

# -------------------------------------------------
# 4. Normalize ด้วย train เท่านั้น
# -------------------------------------------------
scaler_X = MinMaxScaler()
scaler_y = MinMaxScaler()

X_train_scaled = scaler_X.fit_transform(df_train[features])
y_train_scaled = scaler_y.fit_transform(df_train[targets])

X_test_scaled = scaler_X.transform(df_test[features])
y_test_scaled = scaler_y.transform(df_test[targets])

# บันทึก scalers
joblib.dump(scaler_X, 'outputs/feature_scaler.pkl')
joblib.dump(scaler_y, 'outputs/target_scaler.pkl')
print("\n✅ บันทึก scalers เรียบร้อย!")

# -------------------------------------------------
# 5. สร้าง sequences
# -------------------------------------------------
n_steps = 7

def create_sequences(X, y, n_steps):
    X_seq, y_seq = [], []
    for i in range(len(X) - n_steps):
        X_seq.append(X[i:i+n_steps])
        y_seq.append(y[i+n_steps])
    return np.array(X_seq), np.array(y_seq)

# Train sequences
X_train_seq, y_train_seq = create_sequences(X_train_scaled, y_train_scaled, n_steps)

# Test sequences (รวม 7 วันสุดท้ายของ train)
last_n_X = X_train_scaled[-n_steps:]
last_n_y = y_train_scaled[-n_steps:]

combined_X_test = np.vstack([last_n_X, X_test_scaled])
combined_y_test = np.vstack([last_n_y, y_test_scaled])

X_test_seq, y_test_seq = create_sequences(combined_X_test, combined_y_test, n_steps)

print(f"\n🧮 ขนาดข้อมูล:")
print(f"   X_train: {X_train_seq.shape}, y_train: {y_train_seq.shape}")
print(f"   X_test:  {X_test_seq.shape}, y_test:  {y_test_seq.shape}")

# -------------------------------------------------
# 6. สร้างและเทรนโมเดล Multi-Output
# -------------------------------------------------
n_features = len(features)
n_outputs = len(targets)

model = Sequential([
    LSTM(128, activation='tanh', return_sequences=True, input_shape=(n_steps, n_features)),
    Dropout(0.2),
    LSTM(64, activation='tanh', return_sequences=False),
    Dropout(0.2),
    Dense(32, activation='relu'),
    Dense(n_outputs)  # 4 outputs
])

model.compile(optimizer='adam', loss='mse')

print("\n🚀 เริ่มเทรนโมเดล...")
history = model.fit(
    X_train_seq, y_train_seq,
    validation_data=(X_test_seq, y_test_seq),
    epochs=50,
    batch_size=16,
    verbose=1
)

# บันทึกโมเดล
model.save('outputs/lstm_multivariate_3outputs.keras')
print("\n✅ บันทึกโมเดลเรียบร้อย!")

# -------------------------------------------------
# 7. ทำนายและประเมินผล
# -------------------------------------------------
y_pred_scaled = model.predict(X_test_seq, verbose=0)

# แปลงกลับเป็นค่าจริง
y_test_actual = scaler_y.inverse_transform(y_test_seq)
y_pred_actual = scaler_y.inverse_transform(y_pred_scaled)

# คำนวณเมตริกสำหรับแต่ละ target
units = {
    'temperature_F': '°F',
    'humidity_%': '%',
    'wind_speed_kmh': 'km/h',
    'pressure_in': 'in'
}

print("\n" + "="*60)
print("📈 ผลประเมินบนปี 2024 (Actual vs Predicted)")
print("="*60)

results = {}
for i, target in enumerate(targets):
    rmse = sqrt(mean_squared_error(y_test_actual[:, i], y_pred_actual[:, i]))
    mae = mean_absolute_error(y_test_actual[:, i], y_pred_actual[:, i])
    mape = mean_absolute_percentage_error(y_test_actual[:, i], y_pred_actual[:, i])
    results[target] = {'RMSE': rmse, 'MAE': mae, 'MAPE': mape}
    
    print(f"\n{target} ({units[target]}):")
    print(f"   RMSE: {rmse:.4f} {units[target]}")
    print(f"   MAE:  {mae:.4f} {units[target]}")
    print(f"   MAPE: {mape:.4f} ({mape*100:.2f}%)")

# -------------------------------------------------
# 8. บันทึกผลเปรียบเทียบ
# -------------------------------------------------
df_compare = pd.DataFrame({
    'date': df_test['datetime'].values
})
for i, target in enumerate(targets):
    df_compare[f'actual_{target}'] = y_test_actual[:, i]
    df_compare[f'predicted_{target}'] = y_pred_actual[:, i]

df_compare.to_csv('outputs/2024_actual_vs_predicted_4vars.csv', index=False)
print("\n✅ บันทึกผลเปรียบเทียบ -> outputs/2024_actual_vs_predicted_4vars.csv")

# -------------------------------------------------
# 9. พล็อตกราฟเปรียบเทียบ
# -------------------------------------------------
fig, axes = plt.subplots(4, 1, figsize=(14, 16), sharex=True)

for i, target in enumerate(targets):
    axes[i].plot(df_compare['date'], df_compare[f'actual_{target}'], 
                 label=f'Actual {target}', color='tab:blue', linewidth=1.2)
    axes[i].plot(df_compare['date'], df_compare[f'predicted_{target}'], 
                 label=f'Predicted {target}', color='tab:orange', linestyle='--', linewidth=1.2)
    axes[i].set_ylabel(f'{target}\n({units[target]})', fontsize=12)
    axes[i].legend(loc='upper right')
    axes[i].grid(True, linestyle='--', alpha=0.6)

axes[3].set_xlabel('Date', fontsize=12)
plt.suptitle('LSTM Multi-Output Forecast vs Actual (Year 2024)', fontsize=16)
plt.tight_layout(rect=[0, 0, 1, 0.97])
plt.show()

# -------------------------------------------------
# 10. (ทางเลือก) พล็อต Loss
# -------------------------------------------------
plt.figure(figsize=(10, 4))
plt.plot(history.history['loss'], label='Training Loss')
plt.plot(history.history['val_loss'], label='Validation Loss')
plt.title('Model Loss During Training')
plt.xlabel('Epoch')
plt.ylabel('Loss (MSE)')
plt.legend()
plt.grid(True, linestyle='--', alpha=0.6)
plt.tight_layout()
plt.show()
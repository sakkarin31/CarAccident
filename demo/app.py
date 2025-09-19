# app.py
# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
from inference_next import prepare_features, predict_next, SEQ_LENGTH, FEATURE_COLS

st.set_page_config(page_title="Accident Risk (LSTM MT)", layout="centered")
st.title("🚦 Accident Risk & Traffic Predictor (Multi-task LSTM)")

st.markdown("""
อัปโหลดไฟล์ CSV ที่มีคอลัมน์อย่างน้อย:
- เวลา: `datetime` **หรือ** (`date` + `time`) อย่างใดอย่างหนึ่ง
- ฟีเจอร์สภาพอากาศ/อื่น ๆ ที่โมเดลใช้ (เช่น `temperature_F`, `humidity_%`, `pressure_in`, `กลางวันกลางคืน`, `condition`)
> ระบบจะจัด one-hot ของ `condition` และคอลัมน์เวลา (`hour/day_of_week/month`) ให้อัตโนมัติ และจัดเรียงคอลัมน์ให้ตรงกับตอนเทรน
""")

file = st.file_uploader("อัปโหลด CSV", type=["csv"])
if not file:
    st.info("รออัปโหลดไฟล์..."); st.stop()

try:
    df_raw = pd.read_csv(file)
except Exception as e:
    st.error(f"อ่านไฟล์ไม่สำเร็จ: {e}")
    st.stop()

# เตรียมฟีเจอร์ให้ตรงกับที่โมเดลต้องการ
try:
    df = prepare_features(df_raw)
except Exception as e:
    st.error(f"เตรียมฟีเจอร์ไม่สำเร็จ: {e}")
    st.stop()

st.write("🎛️ คอลัมน์ที่โมเดลใช้ (เรียงตามที่เทรน):")
st.code(", ".join(FEATURE_COLS), language="text")

if len(df) < SEQ_LENGTH:
    st.error(f"ข้อมูลมี {len(df)} แถว แต่ต้องการอย่างน้อย {SEQ_LENGTH} แถวเพื่อทำหน้าต่างลำดับ")
    st.stop()

window = df.tail(SEQ_LENGTH)  # เรียงเวลาแล้วจาก prepare_features
st.write(f"หน้าต่างล่าสุด {SEQ_LENGTH} แถว:")
st.dataframe(window.tail(SEQ_LENGTH))

# ทำนาย
try:
    prob, flag, counts = predict_next(window)
except Exception as e:
    st.error(f"ทำนายไม่สำเร็จ: {e}")
    st.stop()

st.subheader("ผลการทำนาย")
col1, col2 = st.columns(2)
with col1:
    st.metric("Prob(เกิดเหตุ)", f"{prob:.3f}")
with col2:
    st.metric("Alert", "⚠️ YES" if flag else "OK")

st.write("คาดการณ์จำนวนรถ [<4, =4, >4]:", counts.tolist())

st.caption("Tip: หาก prob สวิง ลองทำ smoothing/hysteresis หรือเพิ่ม window length ให้ยาวขึ้นตามงานจริง")

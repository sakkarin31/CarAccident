# streamlit run .\app\app.py
import streamlit as st
import geopandas as gpd
import osmnx as ox
import numpy as np
import os
import pydeck as pdk
import pandas as pd
import matplotlib.pyplot as plt

# ตั้งค่า Streamlit
st.set_page_config(layout="wide", page_title="ความเสี่ยงอุบัติเหตุทางหลวง - สงขลา")

st.title("🚦 การทำนายความเสี่ยงอุบัติเหตุทางหลวง - จังหวัดสงขลา")
st.markdown("เลือกถนนในสงขลาเพื่อดูโอกาสเกิดอุบัติเหตุ (แสดงผลเป็นเปอร์เซ็นต์)")

# ------------------- โหลดถนน -------------------
FILE_PATH = "songkhla_roads.geojson"
target_roads = ["4", "42", "43", "407"]

@st.cache_data
def get_roads():
    if os.path.exists(FILE_PATH):
        edges = gpd.read_file(FILE_PATH)
    else:
        G = ox.graph_from_place(
            "Songkhla, Thailand",
            network_type="drive",
            custom_filter='["highway"~"trunk|primary"]'
        )
        edges = ox.graph_to_gdfs(G, nodes=False)
        edges = edges[edges["ref"].isin(target_roads)]
        edges.to_file(FILE_PATH, driver="GeoJSON")
    return edges

edges = get_roads()

# ------------------- ฟังก์ชันสุ่มความเสี่ยง -------------------
def predict_risk(road_id):
    np.random.seed(int(road_id))
    return np.random.uniform(10, 80)

# ------------------- UI -------------------
selected = st.selectbox("เลือกถนนทางหลวง", ["-"] + target_roads, format_func=lambda x: f"ถนนหมายเลข {x}" if x != "-" else "-")


# ------------------- แปลง GeoDataFrame เป็น PathLayer -------------------
def gdf_to_paths(gdf, color, width):
    data = []
    for _, row in gdf.iterrows():
        if row.geometry.geom_type == "LineString":
            coords = [[x, y] for x, y in row.geometry.coords]
            data.append({"path": coords, "color": color, "width": width})
        elif row.geometry.geom_type == "MultiLineString":
            for part in row.geometry:
                coords = [[x, y] for x, y in part.coords]
                data.append({"path": coords, "color": color, "width": width})
    return data

# ถนนทั้งหมดสีเทา (ความหนา 4)
all_paths = gdf_to_paths(edges, [150, 150, 150], 4)

# ถนนที่เลือกสีแดง (ความหนา 10)
highlight_paths = []
if selected != "-":
    highlight_paths = gdf_to_paths(edges[edges["ref"] == selected], [255, 0, 0], 12)

# ------------------- สร้าง Layers -------------------
layers = [
    pdk.Layer(
        "PathLayer",
        data=all_paths,
        get_path="path",
        get_color="color",
        get_width="width",
    )
]

if highlight_paths:
    layers.append(
        pdk.Layer(
            "PathLayer",
            data=highlight_paths,
            get_path="path",
            get_color="color",
            get_width="width",
            pickable=True
        )
    )

# ------------------- ViewState -------------------
view_state = pdk.ViewState(latitude=7.2, longitude=100.6, zoom=9)

# ------------------- แสดงแผนที่ -------------------
st.pydeck_chart(
    pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        map_style=pdk.map_styles.LIGHT  # แผนที่สว่าง
    ),
    height=600,
    width=800
)

# ------------------- แสดงผลการทำนาย -------------------
if selected != "-":
    risk = predict_risk(selected)
    st.markdown(
        f"### 📊 ผลการทำนาย\n**ถนนหมายเลข {selected}** "
        f"มีโอกาสเกิดอุบัติเหตุประมาณ "
        f"<span style='color:red; font-size:28px;'>{risk:.2f}%</span>",
        unsafe_allow_html=True
    )


# -----------------------------------------------------------------------------------------------



    st.header("📂 ข้อมูลอุบัติเหตุ 2024")
# โหลด accident2024.csv
accident_df = pd.read_csv("dataset/accident2024.csv")
st.dataframe(accident_df)

# ------------------- ส่วนที่ 2: กราฟอากาศ -------------------

# ------------------- ทำความสะอาดข้อมูลอากาศ -------------------

def clean_numeric(series):
    """ลบ % หรือ ตัวอักษรอื่น ๆ แล้วแปลงเป็น float"""
    return (
        series.astype(str)
        .str.replace("%", "", regex=False)
        .str.replace("°F", "", regex=False)
        .str.replace("in", "", regex=False)
        .str.strip()
        .replace("", np.nan)     # ถ้าเป็นค่าว่างให้เป็น NaN
        .astype(float)
    )

st.header("🌤️ ข้อมูลสภาพอากาศจังหวัดสงขลา 2024")
weather_df = pd.read_csv("dataset/songkhla_weather_2024_01.csv")

# ------------------- ส่วนที่ 2: กราฟอากาศ -------------------
st.header("🌤️ ข้อมูลสภาพอากาศจังหวัดสงขลา 2024")

# แปลงวันที่
if "date" in weather_df.columns:
    weather_df["date"] = pd.to_datetime(weather_df["date"], errors="coerce")
    weather_df["month"] = weather_df["date"].dt.month
else:
    st.warning("⚠️ ไม่มี column date ในไฟล์สภาพอากาศ")
    weather_df["month"] = 1  # fallback

# เลือกตัวแปร
option = st.radio("เลือกตัวแปร", ["temperature_F", "humidity_%", "pressure_in"])

# เลือกเดือน
months = sorted(weather_df["month"].unique())
month_selected = st.selectbox("เลือกเดือน", months, format_func=lambda m: f"เดือน {m}")

# กรองเฉพาะเดือนที่เลือก
monthly_data = weather_df[weather_df["month"] == month_selected]

# วาดกราฟ
fig, ax = plt.subplots(figsize=(10,4))
ax.plot(monthly_data["date"], monthly_data[option], label=option, color="tab:blue", marker="o", linestyle="-")
ax.set_title(f"{option} month {month_selected}", fontsize=14)
ax.set_xlabel("day")
ax.set_ylabel(option)
ax.legend()
st.pyplot(fig)



# ------------------- ส่วนที่ 3: กราฟอุบัติเหตุบนถนนสาย 4 -------------------
st.header("🚗 จำนวนอุบัติเหตุบนถนนสาย 4 (ทั้งปี)")

road4 = accident_df[accident_df["road"] == 4]

if "date" in road4.columns:
    road4["date"] = pd.to_datetime(road4["date"], errors="coerce")
    road4["month"] = road4["date"].dt.to_period("M")

    # สร้างช่วงเดือนตลอดปี 2024
    all_months = pd.period_range("2024-01", "2024-12", freq="M")
    monthly_counts = road4.groupby("month").size().reindex(all_months, fill_value=0)

    fig2, ax2 = plt.subplots(figsize=(10,4))
    monthly_counts.plot(kind="bar", ax=ax2, color="tab:red")
    ax2.set_title("จำนวนอุบัติเหตุบนถนนสาย 4 รายเดือน (ปี 2024)")
    ax2.set_xlabel("เดือน")
    ax2.set_ylabel("จำนวนอุบัติเหตุ")
    st.pyplot(fig2)
else:
    st.warning("⚠️ ไม่มี column วันที่ในไฟล์ accident2024.csv")

# ------------------- ส่วนที่ 4: Histogram รถตามประเภท -------------------
st.header("📊 Histogram ประเภทรถ")

# สมมติว่ามี column 'vehicle_type' ที่แบ่งเป็น "<4ล้อ", "4ล้อ", ">4ล้อ"
if "vehicle_type" in accident_df.columns:
    fig3, ax3 = plt.subplots(figsize=(6,4))
    accident_df["vehicle_type"].value_counts().plot(kind="bar", ax=ax3, color="tab:green")
    ax3.set_title("จำนวนรถแต่ละประเภทในปี 2024")
    ax3.set_ylabel("จำนวน")
    st.pyplot(fig3)
else:
    st.warning("⚠️ ไม่มี column vehicle_type ใน accident2024.csv")
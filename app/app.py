import streamlit as st
import geopandas as gpd
import osmnx as ox
import numpy as np
import os
import pydeck as pdk
import pandas as pd
import matplotlib.pyplot as plt

# ------------------- ตั้งค่า Streamlit -------------------
st.set_page_config(page_title="Highway Accident Risk - Songkhla", layout="wide")

st.title("🚦 Highway Accident Risk Prediction - Songkhla Province")

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

# ------------------- GeoDataFrame -> PathLayer -------------------
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

def clean_numeric(series):
    return (series.astype(str)
            .str.replace("%", "", regex=False)
            .str.replace("°F", "", regex=False)
            .str.replace("in", "", regex=False)
            .str.strip()
            .replace("", np.nan) # ถ้าเป็นค่าว่างให้เป็น NaN
            .astype(float)
    )
    # """ลบ % หรือ ตัวอักษรอื่น ๆ แล้วแปลงเป็น float"""



    
# ถนนทั้งหมดสีเทา
all_paths = gdf_to_paths(edges, [150, 150, 150], 4)



# ------------------- Tabs -------------------
tab1, tab2 = st.tabs(["🗺️ Map & Prediction", "📊 Data Analysis"])

# ------------------- Tab 1: Map & Prediction -------------------
with tab1:
    col1, col2 = st.columns([2, 1])

    with col1:
        # ------------------- UI เลือกถนน -------------------
        selected = st.selectbox(
            "เลือกถนนทางหลวง",
            ["-"] + target_roads,
            format_func=lambda x: f"ถนนหมายเลข {x}" if x != "-" else "-"
        )

        # ถนนที่เลือกสีแดง
        highlight_paths = []
        if selected != "-":
            highlight_paths = gdf_to_paths(edges[edges["ref"] == selected], [255, 0, 0], 12)

        # ------------------- Layers -------------------
        layers = [
            pdk.Layer("PathLayer", data=all_paths, get_path="path", get_color="color", get_width="width")
        ]
        if highlight_paths:
            layers.append(
                pdk.Layer("PathLayer", data=highlight_paths, get_path="path", get_color="color", get_width="width", pickable=True)
            )

        # ------------------- ViewState -------------------
        view_state = pdk.ViewState(latitude=7.2, longitude=100.6, zoom=9)

        st.subheader("🗺️ Road Map")
        st.pydeck_chart(
            pdk.Deck(layers=layers, initial_view_state=view_state, map_style=pdk.map_styles.LIGHT),
            height=600,
        )

    with col2:
        st.subheader("📊 Accident Risk Prediction")
        if selected != "-":
            risk = predict_risk(selected)
            st.metric(label=f"Road {selected}", value=f"{risk:.2f} %", delta="Predicted Risk")
        else:
            st.info("Please select a road from the dropdown.")

# ------------------- Tab 2: Data Analysis -------------------
with tab2:
    st.subheader("📊 Data Analysis for Songkhla")

    sub_tab1, sub_tab2, sub_tab3, sub_tab4 = st.tabs([
        "📂 Accident Data 2024",
        "🌤️ Weather Data 2024",
        "🚗 Road 4 Monthly Accidents",
        "🚗 Vehicle-type on Road 4 "
    ])

    # -------- Accident Data 2024 --------
    with sub_tab1:
        st.header("📂 Accident Data 2024")
        accident_df = pd.read_csv("dataset/accident2024.csv")
        st.dataframe(accident_df)

    # -------- Weather Data 2024 --------
    with sub_tab2:
        st.header("🌤️ ข้อมูลสภาพอากาศจังหวัดสงขลา 2024")
        weather_df = pd.read_csv("dataset/songkhla_weather_2024_01.csv")

        # รวม date + time → datetime
        weather_df["datetime"] = pd.to_datetime(
            weather_df["date"].astype(str) + " " + weather_df["time"].astype(str),
            errors="coerce"
        )
        weather_df["date_only"] = weather_df["datetime"].dt.date

        # ทำความสะอาดค่าตัวเลข
        weather_df["temperature_F"] = clean_numeric(weather_df["temperature_F"])
        weather_df["humidity_%"] = clean_numeric(weather_df["humidity_%"])
        weather_df["pressure_in"] = clean_numeric(weather_df["pressure_in"])

        # ตัวแปรที่ต้องการดู (เปลี่ยนเป็น selectbox)
        option = st.selectbox(
            "เลือกตัวแปร",
            ["temperature_F", "humidity_%", "pressure_in"],
            format_func=lambda x: {
                "temperature_F": "อุณหภูมิ (°F)",
                "humidity_%": "ความชื้น (%)",
                "pressure_in": "ความกดอากาศ (inHg)"
            }.get(x, x)
        )

        # วันที่ที่มีข้อมูล
        unique_dates = sorted(weather_df["date_only"].dropna().unique())
        selected_date = st.date_input(
            "เลือกวันที่",
            value=unique_dates[0],
            min_value=min(unique_dates),
            max_value=max(unique_dates)
        )

        # กรองเฉพาะวันนั้น
        daily_data = weather_df[weather_df["date_only"] == selected_date].copy()

        # วาดกราฟ
        if not daily_data.empty:
            fig, ax = plt.subplots(figsize=(12, 5))

            # line อย่างเดียว
            ax.plot(daily_data["datetime"], daily_data[option], linestyle="-")

            ax.set_xlabel("Time")
            ax.set_ylabel(option)
            ax.set_title(f"{option} in {selected_date}")

            # ให้แกน X โชว์เฉพาะเวลา
            ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter("%H:%M"))

            plt.xticks(rotation=45)
            st.pyplot(fig)
        else:
            st.warning("ไม่มีข้อมูลสำหรับวันที่เลือก")

    # -------- Road 4 Monthly Accidents --------
    with sub_tab3:
        st.header("🚗 Number of Accidents on Road 4 (by Month)")

        # โหลดข้อมูล
        accident_df = pd.read_csv("dataset/accident2024.csv")
        accident_df["วันที่เกิดเหตุ"] = pd.to_datetime(accident_df["วันที่เกิดเหตุ"], errors="coerce")
        accident_df["month"] = accident_df["วันที่เกิดเหตุ"].dt.month

        # กรองเฉพาะสายทาง 4
        road4 = accident_df[accident_df["รหัสสายทาง"] == 4]

        monthly_counts = road4.groupby("month").size()

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(monthly_counts.index, monthly_counts.values, marker="o", linestyle="-")
        ax.set_xlabel("Month")
        ax.set_ylabel("Number of Accidents")
        ax.set_title("Monthly Accidents on Road 4 (2024)")
        ax.set_xticks(range(1, 13))
        st.pyplot(fig)


    with sub_tab4:
        st.header("🚗 Number of Vehicle-type on Road 4 (by Year)")
        # คำนวณจำนวนรถแต่ละประเภทตลอดปี
        vehicle_cols = ["รถน้อยกว่า4ล้อ", "รถ4ล้อ", "รถมากกว่า4ล้อ"]
        existing_cols = [c for c in vehicle_cols if c in accident_df.columns]

        if existing_cols:
            vehicle_counts = accident_df[existing_cols].sum()
            # เปลี่ยนชื่อคอลัมน์เป็นอังกฤษ
            rename_map = {
                "รถน้อยกว่า4ล้อ": "Less than 4 wheels",
                "รถ4ล้อ": "4 wheels",
                "รถมากกว่า4ล้อ": "More than 4 wheels"
            }
            vehicle_counts.index = vehicle_counts.index.map(lambda x: rename_map.get(x, x))

            # วาด Histogram
            fig, ax = plt.subplots(figsize=(8,4))
            vehicle_counts.plot(kind="bar", ax=ax, color="tab:purple")
            ax.set_title("Number of Vehicles by Type (2024)")
            ax.set_ylabel("Number of Vehicles")
            st.pyplot(fig)
        else:
            st.warning("Vehicle-type columns not found in accident2024.csv")

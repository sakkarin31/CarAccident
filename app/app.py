import streamlit as st
import geopandas as gpd
import osmnx as ox
import numpy as np
import os
import pydeck as pdk

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
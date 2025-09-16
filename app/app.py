# streamlit run app/app.py
import streamlit as st
import geopandas as gpd
import osmnx as ox
import numpy as np
import os
import pydeck as pdk
import pandas as pd
import matplotlib.pyplot as plt
from streamlit_folium import st_folium
import folium
import networkx as nx
from itertools import islice

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

# ------------------- โหลดกราฟ OSM สำหรับหาเส้นทาง -------------------
@st.cache_resource
def load_graph():
    return ox.graph_from_place(
        "Songkhla, Thailand",
        network_type="drive",
        custom_filter='["highway"~"trunk|primary"]'
    )

G = load_graph()

# ------------------- ฟังก์ชันสุ่มความเสี่ยง -------------------
def predict_risk(road_id):
    try:
        np.random.seed(int(road_id))
    except:
        np.random.seed(42)
    return np.random.uniform(1, 10)

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
            .replace("", np.nan)
            .astype(float)
    )

def route_to_gdf(G, route):
    # คืน GeoDataFrame ของเส้นทางจากรายการ node
    edge_gdf = ox.graph_to_gdfs(G, nodes=False)
    edge_pairs = set(zip(route[:-1], route[1:]))

    def is_in_route(row):
        u, v = row.name[0], row.name[1]
        return (u, v) in edge_pairs or (v, u) in edge_pairs

    route_edges = edge_gdf[edge_gdf.apply(is_in_route, axis=1)]
    return route_edges.reset_index(drop=True)

def snap_to_road(G, lat, lon):
    node = ox.distance.nearest_nodes(G, lon, lat)
    x = G.nodes[node]["x"]
    y = G.nodes[node]["y"]
    return {"lat": y, "lng": x}

def simplify_graph(G):
    H = nx.DiGraph()
    for u, v, data in G.edges(data=True):
        length = data.get("length", 1)
        if H.has_edge(u, v):
            if length < H[u][v]["length"]:
                H[u][v].update(data)
        else:
            H.add_edge(u, v, **data)
    return H

def k_shortest_paths(G, source, target, k=3, weight="length"):
    if G.is_multigraph():
        G = simplify_graph(G)
    return list(islice(nx.shortest_simple_paths(G, source, target, weight=weight), k))

def get_route_length(G, route):
    """Calculate total length of a route"""
    length = 0
    for u, v in zip(route[:-1], route[1:]):
        if G.has_edge(u, v):
            data = min(G[u][v].values(), key=lambda x: x.get("length", 0))
            length += data.get("length", 0)
    return length

# ------------------- ถนนทั้งหมดสีเทา -------------------
all_paths = gdf_to_paths(edges, [150, 150, 150], 4)

# ------------------- Tabs -------------------
tab1, tab2 = st.tabs(["🗺️ Map & Prediction", "📊 Data Analysis"])

# ------------------- Tab 1: Map & Prediction -------------------
with tab1:
    st.subheader("📍 Click on the map to select route points (Start → ... → End)")

    # เก็บ state ของหมุด
    if "points" not in st.session_state:
        st.session_state["points"] = []

    # สร้างแผนที่เริ่มต้น
    m = folium.Map(location=[7.2, 100.6], zoom_start=9)

    # วาง marker เดิมที่เคยเลือก
    for i, p in enumerate(st.session_state["points"]):
        folium.Marker([p["lat"], p["lng"]], popup=f"Point {i+1}").add_to(m)

    out = st_folium(m, width=800, height=500, key="map")

    # คลิกเพิ่มจุด
    if out and out.get("last_clicked"):
        raw = out["last_clicked"]
        snapped = snap_to_road(G, raw["lat"], raw["lng"])
        if snapped not in st.session_state["points"]:
            st.session_state["points"].append(snapped)
            st.rerun()

    # ปุ่มล้างจุด
    if st.button("🗑 Clear Points"):
        st.session_state["points"] = []
        st.rerun()

    # แสดงจุดที่เลือก
    if st.session_state["points"]:
        st.write("**Selected Points:**")
        for i, p in enumerate(st.session_state["points"], start=1):
            st.write(f"- Point {i}: ({p['lat']:.6f}, {p['lng']:.6f})")

    # ---------------- คำนวณเส้นทาง ----------------
    if len(st.session_state["points"]) >= 2:
        start = st.session_state["points"][0]
        end = st.session_state["points"][-1]

        orig = ox.distance.nearest_nodes(G, start["lng"], start["lat"])
        dest = ox.distance.nearest_nodes(G, end["lng"], end["lat"])

        # หา k shortest paths
        k = 3
        paths = k_shortest_paths(G, orig, dest, k=k, weight="length")

        # วัดความยาวและ risk
        path_infos = []
        for path in paths:
            # คำนวณระยะทาง
            length = 0
            for u, v in zip(path[:-1], path[1:]):
                if G.has_edge(u, v):
                    data = min(G[u][v].values(), key=lambda x: x.get("length", 0))
                    length += data.get("length", 0)

            # คำนวณ risk
            gdf = route_to_gdf(G, path)
            risks = []
            for _, row in gdf.iterrows():
                road_id = row.get("ref", "0") or "0"
                risks.append(predict_risk(road_id))
            avg_risk = np.mean(risks) if risks else 0

            path_infos.append({"nodes": path, "length": length, "avg_risk": avg_risk})

        # เรียงจากสั้น → ยาว
        path_infos = sorted(path_infos, key=lambda x: x["length"])

        # UI เลือกเส้นทาง
        options = [f"Route {i+1} - {info['length']:.0f} m | Risk {info['avg_risk']:.2f}%"
                   for i, info in enumerate(path_infos)]
        choice = st.selectbox("Select a route:", options)
        chosen = path_infos[options.index(choice)]

        # ---------------- วาดแผนที่ ----------------
        m = folium.Map(location=[start["lat"], start["lng"]], zoom_start=12)

        # วาดถนนทั้งหมดสีเทา
        for _, row in edges.iterrows():
            if row.geometry.geom_type == "LineString":
                folium.PolyLine([(y, x) for x, y in row.geometry.coords],
                                color="gray", weight=2, opacity=0.3).add_to(m)

        # วาดทุกเส้นทาง (สีต่างกัน)
        colors = ["blue", "green", "purple"]
        for i, info in enumerate(path_infos):
            gdf = route_to_gdf(G, info["nodes"])
            for _, row in gdf.iterrows():
                if row.geometry.geom_type == "LineString":
                    folium.PolyLine([(y, x) for x, y in row.geometry.coords],
                                    color=colors[i % len(colors)], weight=4,
                                    opacity=0.6,
                                    popup=f"Route {i+1} - {info['length']:.0f} m").add_to(m)

        # ไฮไลท์เส้นทางที่เลือก (แดงหนา)
        chosen_gdf = route_to_gdf(G, chosen["nodes"])
        for _, row in chosen_gdf.iterrows():
            if row.geometry.geom_type == "LineString":
                folium.PolyLine([(y, x) for x, y in row.geometry.coords],
                                color="red", weight=7, opacity=1,
                                popup=f"Chosen Route - {chosen['length']:.0f} m").add_to(m)

        # Marker ของทุกจุด
        for i, p in enumerate(st.session_state["points"]):
            folium.Marker([p["lat"], p["lng"]], popup=f"Point {i+1}").add_to(m)

        # แสดงผล Risk และ Length
        st.metric("Route Length", f"{chosen['length']:.0f} m")
        st.metric("Predicted Risk (Avg)", f"{chosen['avg_risk']:.2f} %")

        st_folium(m, width=800, height=500)


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
        st.header("🌤️ Songkhla Weather Data 2024")
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

        option = st.selectbox("Select Variable", ["temperature_F", "humidity_%", "pressure_in"])
        unique_dates = sorted(weather_df["date_only"].dropna().unique())
        selected_date = st.date_input("Select Date", value=unique_dates[0])

        daily_data = weather_df[weather_df["date_only"] == selected_date].copy()

        if not daily_data.empty:
            fig, ax = plt.subplots(figsize=(12, 5))
            ax.plot(daily_data["datetime"], daily_data[option], linestyle="-")
            ax.set_xlabel("Time")
            ax.set_ylabel(option)
            ax.set_title(f"{option} in {selected_date}")
            ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter("%H:%M"))
            plt.xticks(rotation=45)
            st.pyplot(fig)
        else:
            st.warning("No data for selected date")

    # -------- Road 4 Monthly Accidents --------
    with sub_tab3:
        st.header("🚗 Total Vehicles Involved in Accidents on Road 4 (by Month)")
        accident_df = pd.read_csv("dataset/accident2024.csv")
        accident_df["วันที่เกิดเหตุ"] = pd.to_datetime(accident_df["วันที่เกิดเหตุ"], errors="coerce", dayfirst=True)
        accident_df["month"] = accident_df["วันที่เกิดเหตุ"].dt.month
        road4 = accident_df[accident_df["รหัสสายทาง"] == 4]
        monthly_counts = road4.groupby("month")["รถที่เกิดเหตุ"].sum().reset_index()
        month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(monthly_counts["month"], monthly_counts["รถที่เกิดเหตุ"], color="tab:blue")
        ax.set_xlabel("Month")
        ax.set_ylabel("Total Vehicles Involved")
        ax.set_title("Monthly Vehicles Involved in Accidents on Road 4 (2024)")
        ax.set_xticks(range(1, 13))
        ax.set_xticklabels(month_names)
        st.pyplot(fig)

    # -------- Vehicle-type on Road 4 --------
    with sub_tab4:
        st.header("🚗 Number of Vehicle-type on Road 4 (by Month/Year)")
        num_car_df = pd.read_csv("dataset/acc_weather-final.csv")
        num_car_df["date"] = pd.to_datetime(num_car_df["date"], errors="coerce")
        num_car_df["month"] = num_car_df["date"].dt.month
        vehicle_cols = ["รถน้อยกว่า4ล้อทั้งหมด", "รถ4ล้อทั้งหมด", "รถมากกว่า4ล้อทั้งหมด"]
        existing_cols = [c for c in vehicle_cols if c in num_car_df.columns]
        if existing_cols:
            month_names = ["All","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
            selected_month = st.selectbox("Select Month", month_names)
            if selected_month != "All":
                month_idx = month_names.index(selected_month)
                filtered_df = num_car_df[num_car_df["month"] == month_idx]
            else:
                filtered_df = num_car_df.copy()
            vehicle_counts = filtered_df[existing_cols].sum()
            rename_map = {
                "รถน้อยกว่า4ล้อทั้งหมด": "Less than 4 wheels",
                "รถ4ล้อทั้งหมด": "4 wheels",
                "รถมากกว่า4ล้อทั้งหมด": "More than 4 wheels"
            }
            vehicle_counts.index = vehicle_counts.index.map(lambda x: rename_map.get(x, x))
            fig, ax = plt.subplots(figsize=(8, 4))
            colors = ["tab:blue", "tab:orange", "tab:green"]
            ax.bar(vehicle_counts.index, vehicle_counts.values, color=colors[:len(vehicle_counts)])
            ax.set_title(f"Number of Vehicles by Type ({selected_month} 2024)")
            ax.set_ylabel("Number of Vehicles")
            ax.set_xlabel("Vehicle Type")
            ax.set_xticks(range(len(vehicle_counts.index)))
            ax.set_xticklabels(vehicle_counts.index, rotation=0)
            st.pyplot(fig)
        else:
            st.warning("Vehicle-type columns not found in accident2024.csv")

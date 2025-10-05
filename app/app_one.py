import streamlit as st
import osmnx as ox
import geopandas as gpd
import networkx as nx
import folium
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium
from shapely.geometry import Point, LineString
import numpy as np
import pandas as pd
import matplotlib.colors as mcolors

# ---------------------------
# โหลดเฉพาะถนนทางหลวงหมายเลข 4
# ---------------------------
@st.cache_data
def load_highway4_graph():
    try:
        custom_filter = '["highway"~"motorway|trunk|primary"]["ref"="4"]'
        G = ox.graph_from_place(
            "Songkhla, Thailand",
            network_type="drive",
            simplify=True,
            custom_filter=custom_filter
        )
        return G
    except Exception as e:
        st.error(f"❌ โหลดกราฟไม่สำเร็จ: {e}")
        return None

# ---------------------------
# ฟังก์ชันวาด buffer รอบสาย 4
# ---------------------------
def draw_highway4_buffer_only(m, G, buffer_m=1000, simplify_tol=50):
    try:
        if G is None:
            return None

        edges = ox.graph_to_gdfs(G, nodes=False, edges=True)
        if edges.empty:
            st.warning("⚠️ ไม่มีข้อมูลถนนสาย 4 ในพื้นที่นี้")
            return None

        if edges.crs is None:
            edges = edges.set_crs(epsg=4326)

        gdf_proj = edges.to_crs(epsg=3857)
        buffer_union = gdf_proj.buffer(buffer_m).unary_union
        buffer_simplified = gpd.GeoSeries([buffer_union], crs="EPSG:3857").simplify(simplify_tol)
        buffer_wgs = buffer_simplified.to_crs(epsg=4326)

        folium.GeoJson(
            buffer_wgs.__geo_interface__,
            name="Highway 4 Buffer",
            style_function=lambda x: {
                "color": "blue",
                "weight": 2,
                "fillColor": "lightblue",
                "fillOpacity": 0.2,
            },
            tooltip=f"พื้นที่รอบถนนสาย 4 (±{buffer_m} m)"
        ).add_to(m)

        return buffer_wgs.iloc[0]

    except Exception as e:
        st.error(f"❌ โหลดขอบเขตไม่สำเร็จ: {e}")
        return None

# ---------------------------
# ฟังก์ชันช่วยเหลือ
# ---------------------------
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

def route_to_gdf(G, nodes):
    edges = []
    geoms = []
    for u, v in zip(nodes[:-1], nodes[1:]):
        if G.has_edge(u, v):
            data = min(G.get_edge_data(u, v).values(), key=lambda d: d.get("length", 1))
            edges.append(data)
            geom = data.get("geometry", LineString([
                (G.nodes[u]["x"], G.nodes[u]["y"]),
                (G.nodes[v]["x"], G.nodes[v]["y"])
            ]))
            geoms.append(geom)
    if not edges:
        return gpd.GeoDataFrame()
    return gpd.GeoDataFrame(edges, geometry=geoms, crs="EPSG:4326")

def get_route_length_meters(gdf):
    if gdf.empty:
        return 0
    gdf = gdf.to_crs(epsg=3857)
    return gdf.length.sum()

def predict_risk(ref):
    return np.random.uniform(0, 10)

def calc_weighted_risk(route_gdf):
    if route_gdf.empty:
        return 0.0
    gdf_proj = route_gdf.to_crs(epsg=3857)
    total_len = gdf_proj.length.sum()
    if total_len == 0:
        return 0.0
    risk_sum = 0.0
    for _, row in gdf_proj.iterrows():
        hw_type = row.get("highway", "")
        if isinstance(hw_type, list):
            hw_type = hw_type[0] if hw_type else ""
        seg_len = row.geometry.length
        if hw_type in {"motorway", "trunk", "primary"}:
            ref = row.get("ref", "unknown")
            risk = predict_risk(ref)
            risk_sum += risk * seg_len
    return risk_sum / total_len

def draw_route_colored(m, gdf):
    if gdf is None or gdf.empty:
        return
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None:
            continue
        hw_type = row.get("highway", "")
        if isinstance(hw_type, list):
            hw_type = hw_type[0] if hw_type else ""
        color = "gray"
        if hw_type in {"motorway", "trunk", "primary"}:
            ref = row.get("ref", "unknown")
            risk = predict_risk(ref)
            norm_risk = min(max(risk / 10, 0), 1)
            rgba = mcolors.to_rgba("red", alpha=0.3 + 0.7 * norm_risk)
            color = mcolors.to_hex(rgba)
        folium.PolyLine(
            [(y, x) for x, y in row.geometry.coords],
            color=color, weight=5, opacity=0.9,
            popup=f"{hw_type or 'non-highway'}"
        ).add_to(m)

# ---------------------------
# Streamlit UI
# ---------------------------
st.set_page_config(page_title="🚗 Highway 4 (Songkhla)", layout="wide")
st.title("🚗 วิเคราะห์ความเสี่ยงบนทางหลวงหมายเลข 4 (Songkhla)")

G = load_highway4_graph()

if G is None:
    st.error("ไม่สามารถโหลดข้อมูลทางหลวงหมายเลข 4 ได้")
else:
    edges = ox.graph_to_gdfs(G, nodes=False, edges=True).to_crs(epsg=4326)

    if "points" not in st.session_state:
        st.session_state["points"] = []

    col_map, col_ctrl = st.columns([3, 1])

    with col_map:
        m = folium.Map(location=[7.1, 100.6], zoom_start=11)

        # ✅ วาด buffer รอบถนนสาย 4
        buffer_geom = draw_highway4_buffer_only(m, G, buffer_m=1000, simplify_tol=50)

        # ✅ วาดเส้นถนนสาย 4
        if not edges.empty:
            folium.GeoJson(
                edges,
                style_function=lambda x: {"color": "gray", "weight": 2},
                name="Highway 4"
            ).add_to(m)

        # ✅ แสดงหมุด
        marker_cluster = MarkerCluster().add_to(m)
        for i, p in enumerate(st.session_state["points"]):
            folium.Marker(
                [p["lat"], p["lng"]],
                popup=f"Point {i+1}",
                tooltip=f"Point {i+1}"
            ).add_to(marker_cluster)

        # ✅ ถ้ามี 2 จุดขึ้นไป คำนวณเส้นทาง
        route_gdf = gpd.GeoDataFrame()
        if len(st.session_state["points"]) >= 2:
            nodes = [ox.distance.nearest_nodes(G, p["lng"], p["lat"]) for p in st.session_state["points"]]
            G_simple = simplify_graph(G)
            full_nodes = []
            for i in range(len(nodes) - 1):
                try:
                    sub_path = nx.shortest_path(G_simple, nodes[i], nodes[i+1], weight="length")
                except nx.NetworkXNoPath:
                    st.warning(f"🚫 No path between Point {i+1} and Point {i+2}")
                    continue
                if i == 0:
                    full_nodes.extend(sub_path)
                else:
                    full_nodes.extend(sub_path[1:])
            route_gdf = route_to_gdf(G, full_nodes)
            draw_route_colored(m, route_gdf)

        out = st_folium(m, width=900, height=600, key="main_map")

    with col_ctrl:
        st.subheader("⚙️ Controls")
        if st.button("🗑 Clear Points"):
            st.session_state["points"] = []
            st.rerun()

        if out and out.get("last_clicked"):
            raw = out["last_clicked"]
            lat, lon = raw["lat"], raw["lng"]
            if buffer_geom is not None and Point(lon, lat).within(buffer_geom):
                node = ox.distance.nearest_nodes(G, lon, lat)
                snapped = {"lat": float(G.nodes[node]["y"]), "lng": float(G.nodes[node]["x"])}
                if snapped not in st.session_state["points"]:
                    st.session_state["points"].append(snapped)
                    st.rerun()
            else:
                st.warning("⚠️ กรุณาปักหมุดภายในพื้นที่รอบถนนสาย 4 เท่านั้น")

        if st.session_state["points"]:
            st.markdown("**📍 Selected Points:**")
            df_points = pd.DataFrame(st.session_state["points"])
            df_points.index = [f"Point {i+1}" for i in range(len(df_points))]
            st.dataframe(df_points)

        if not route_gdf.empty:
            total_len = get_route_length_meters(route_gdf)
            avg_risk = calc_weighted_risk(route_gdf)
            st.metric("📏 Route Length (m)", f"{total_len:.0f}")
            st.metric("⚠️ Predicted Risk", f"{avg_risk:.2f}")

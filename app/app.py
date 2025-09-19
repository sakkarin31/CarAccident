import streamlit as st
import geopandas as gpd
import osmnx as ox
import networkx as nx
import pandas as pd
import numpy as np
import folium
from folium.plugins import MarkerCluster
from shapely.ops import unary_union
from streamlit_folium import st_folium
from shapely.geometry import LineString

# ---------------------------
# โหลดข้อมูลด้วย cache
# ---------------------------
@st.cache_data
def load_graph():
    return ox.graph_from_place(
        "Hat Yai, Songkhla, Thailand", simplify=True, network_type="drive"
    )

@st.cache_data
def load_highways():
    gdf = gpd.read_file("songkhla_roads.geojson").to_crs(epsg=4326)
    if not gdf.empty:
        gdf["geometry"] = gdf["geometry"].simplify(
            tolerance=0.0001, preserve_topology=True
        )
    return gdf

G = load_graph()
highways = load_highways()

# ---------------------------
# ฟังก์ชันช่วย
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
            data_copy = data.copy()
            data_copy["highway"] = data_copy.get("highway", "")
            edges.append(data_copy)
            if "geometry" in data_copy:
                geoms.append(data_copy["geometry"])
            else:
                geoms.append(LineString([
                    (G.nodes[u]["x"], G.nodes[u]["y"]),
                    (G.nodes[v]["x"], G.nodes[v]["y"])
                ]))
    if not edges:
        return gpd.GeoDataFrame()
    return gpd.GeoDataFrame(edges, geometry=geoms, crs="EPSG:4326")

def get_route_length_meters(gdf):
    if gdf.empty:
        return 0
    gdf = gdf.to_crs(epsg=3857)
    return gdf.length.sum()

def predict_risk(ref):
    return np.random.uniform(0, 100)

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

        if hw_type == "motorway":
            color = "blue"
        elif hw_type in {"trunk", "primary"}:
            color = "red"
        elif hw_type in {"secondary", "tertiary"}:
            color = "orange"
        else:
            color = "green"

        lines = [geom] if geom.geom_type == "LineString" else list(geom)
        for line in lines:
            folium.PolyLine(
                [(y, x) for x, y in line.coords],
                color=color, weight=6, opacity=0.8,
                popup=f"{hw_type or 'unknown'}"
            ).add_to(m)

# ✅ ฟังก์ชันคำนวณ % ทางหลวง (รวม buffer)
def calc_highway_ratio(route_gdf, highways, buffer_m=30):
    if route_gdf.empty or highways.empty:
        return 0.0

    route_proj = route_gdf.to_crs(epsg=3857)
    highways_proj = highways.to_crs(epsg=3857)

    route_proj["len"] = route_proj.length
    route_proj["is_hw"] = False

    hw_buffer = highways_proj.buffer(buffer_m).unary_union

    for idx, geom in route_proj.geometry.items():
        if geom.intersects(hw_buffer):
            route_proj.at[idx, "is_hw"] = True
        else:
            hw_type = route_proj.at[idx, "highway"]
            if isinstance(hw_type, list):
                hw_type = hw_type[0] if hw_type else ""
            if hw_type in {"motorway", "trunk", "primary"}:
                route_proj.at[idx, "is_hw"] = True

    hw_len = route_proj.loc[route_proj["is_hw"], "len"].sum()
    total_len = route_proj["len"].sum()
    return hw_len / total_len if total_len > 0 else 0.0

# ---------------------------
# UI
# ---------------------------
st.set_page_config(page_title="🚗 Accident Risk Map", layout="wide")
st.title("🚗 Accident Risk Map (Hat Yai)")

if "points" not in st.session_state:
    st.session_state["points"] = []

col_map, col_ctrl = st.columns([3, 1])

with col_map:
    m = folium.Map(location=[7.01, 100.47], zoom_start=12)

    marker_cluster = MarkerCluster().add_to(m)
    for i, p in enumerate(st.session_state["points"]):
        folium.Marker(
            [p["lat"], p["lng"]],
            popup=f"Point {i+1}",
            tooltip=f"Point {i+1}"
        ).add_to(marker_cluster)

    route_gdf = gpd.GeoDataFrame()
    if len(st.session_state["points"]) >= 2:
        nodes = [ox.distance.nearest_nodes(G, p["lng"], p["lat"]) for p in st.session_state["points"]]
        G_simple = simplify_graph(G)

        full_nodes = []
        for i in range(len(nodes) - 1):
            try:
                sub_path = nx.shortest_path(G_simple, nodes[i], nodes[i+1], weight="length")
            except nx.NetworkXNoPath:
                st.warning(f"No path between Point {i+1} and Point {i+2}")
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
        try:
            node = ox.distance.nearest_nodes(G, raw["lng"], raw["lat"])
            snapped = {"lat": float(G.nodes[node]["y"]), "lng": float(G.nodes[node]["x"])}
        except Exception:
            snapped = {"lat": raw["lat"], "lng": raw["lng"]}
        if snapped not in st.session_state["points"]:
            st.session_state["points"].append(snapped)
            st.rerun()

    if st.session_state["points"]:
        st.markdown("**📍 Selected points:**")
        df_points = pd.DataFrame(st.session_state["points"])
        df_points.index = [f"Point {i+1}" for i in range(len(df_points))]
        st.dataframe(df_points)

    # if st.session_state["points"]:
    #     st.markdown("**📍 Selected points:**")

    #     for i, p in enumerate(st.session_state["points"]):
    #         col1, col2, col3 = st.columns([3, 2, 2])
    #         with col1:
    #             st.write(f"Point {i+1}: {p['lat']:.5f}, {p['lng']:.5f}")
    #         with col2:
    #             if st.button("⬆️ ขึ้น", key=f"up_{i}") and i > 0:
    #                 pts = st.session_state["points"]
    #                 pts[i-1], pts[i] = pts[i], pts[i-1]
    #                 st.session_state["points"] = pts
    #                 st.rerun()
    #         with col3:
    #             if st.button("⬇️ ลง", key=f"down_{i}") and i < len(st.session_state["points"])-1:
    #                 pts = st.session_state["points"]
    #                 pts[i+1], pts[i] = pts[i], pts[i+1]
    #                 st.session_state["points"] = pts
    #                 st.rerun()

    # 📊 คำนวณ metric
    if not route_gdf.empty:
        total_len = get_route_length_meters(route_gdf)

        # ✅ ใช้ฟังก์ชันใหม่
        prop_hw = calc_highway_ratio(route_gdf, highways)

        # ประมาณความเสี่ยงจากถนนทางหลวง
        highways_proj = highways.to_crs(epsg=3857)
        route_proj = route_gdf.to_crs(epsg=3857)
        risk_refs = highways_proj[highways_proj.geometry.intersects(route_proj.unary_union)]
        refs = [r for r in risk_refs.get("ref", []) if pd.notna(r)] if not risk_refs.empty else []
        risks = [predict_risk(r) for r in refs] if refs else [0]
        avg_risk = np.mean(risks)
        overall_risk = avg_risk * prop_hw

        st.metric("📏 Route length (m)", f"{total_len:.0f}")
        st.metric("🛣 % Highway", f"{prop_hw*100:.1f}%")
        st.metric("⚠️ Predicted Risk", f"{overall_risk:.2f}%")

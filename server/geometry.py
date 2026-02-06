import geopandas as gpd
import os
import json
from shapely.ops import unary_union
from shapely.geometry import GeometryCollection, Polygon

# INTERNAL: Load GeoJSON
def _load_geojson(project_id, data_dir):
    path = os.path.join(data_dir, f"{project_id}_map.geojson")
    if not os.path.exists(path):
        raise FileNotFoundError("GeoJSON not found for project")
    return gpd.read_file(path)

def _load_constraints(project_id, config_dir):
    path = os.path.join(config_dir, f"{project_id}_constraints.json")
    if not os.path.exists(path):
        # Fallback if file doesn't exist yet (use defaults)
        return {}
    with open(path) as f:
        return json.load(f)

# ------------------------------------------------------------
# BUILDABLE AREA & GREEN BELT
# ------------------------------------------------------------
def generate_buildable_area(project_id, data_dir, config_dir):
    gdf = _load_geojson(project_id, data_dir)
    
    # LOAD CONSTRAINTS FROM FILE
    constraints = _load_constraints(project_id, config_dir)

    # Extract Parameters
    green_belt_width = float(constraints.get("green_belt_setback_m", 10.0))
    obstacle_buffer = float(constraints.get("buffer_obstacle_m", 3.0))
    
    # Optional extra setback (if user wants building setback ON TOP of green belt)
    # Usually 0 if green belt acts as the setback
    internal_setback = float(constraints.get("setback_boundary_m", 0.0))

    boundary_gdf = gdf[gdf["type"] == "boundary"]
    obstacle_gdf = gdf[gdf["type"] == "obstacle"]
    road_gdf = gdf[gdf["type"] == "road"]

    if boundary_gdf.empty:
        raise ValueError("DXF must contain a boundary polygon")

    # Generate Green Belt (The Ring)
    site_geom = unary_union(boundary_gdf.geometry)
    gross_area = site_geom.area
    
    green_belt_geom = Polygon()
    inner_site_geom = site_geom

    if green_belt_width > 0:
        # Buffer inward to create the inner boundary
        inner_site_geom = site_geom.buffer(-green_belt_width)
        
        # The Green Belt is the difference between Outer and Inner
        green_belt_geom = site_geom.difference(inner_site_geom)

    # --------------------------------------------------
    # 2. Subtract Obstacles & Roads from the Inner Site
    # --------------------------------------------------
    subtract_geoms = []

    # Obstacle buffer
    if not obstacle_gdf.empty:
        obs_geom = unary_union(obstacle_gdf.geometry)
        if obstacle_buffer > 0:
            obs_geom = obs_geom.buffer(obstacle_buffer)
        subtract_geoms.append(obs_geom)

    # Existing Road buffer
    if not road_gdf.empty:
        road_geom = unary_union(road_gdf.geometry)
        # Check if we should buffer existing roads
        road_geom = road_geom.buffer(obstacle_buffer)
        subtract_geoms.append(road_geom)

    if subtract_geoms:
        buildable_geom = inner_site_geom.difference(unary_union(subtract_geoms))
    else:
        buildable_geom = inner_site_geom
        
    if internal_setback > 0:
        buildable_geom = buildable_geom.buffer(-internal_setback)

    buildable_geom = buildable_geom.buffer(0)
    usable_area = buildable_geom.area

    entry_points = []
    if "type" in gdf.columns:
        ep_gdf = gdf[gdf["type"] == "entry_point"]
        if not ep_gdf.empty:
            entry_points = list(ep_gdf.geometry)

    buildable_feature = {
        "type": "Feature",
        "geometry": buildable_geom.__geo_interface__,
        "properties": {
            "type": "buildable_area",
            "green_belt_width_m": green_belt_width,
        }
    }
    
    green_belt_feature = {
        "type": "Feature",
        "geometry": green_belt_geom.__geo_interface__,
        "properties": {
            "type": "green_belt",
            "area_sqm": round(green_belt_geom.area, 2)
        }
    }

    return {
        "raw_geom": buildable_geom,
        "green_belt_geom": green_belt_geom,
        "metrics": { 
            "gross_area_sqm": gross_area,
            "usable_area_sqm": usable_area,
            "green_belt_area_sqm": green_belt_geom.area
        },
        "feature": buildable_feature,
        "green_belt_feature": green_belt_feature,
        "entry_points": entry_points
    }

def generate_main_road(project_id, data_dir, config_dir, site_geom):
    gdf = _load_geojson(project_id, data_dir)
    road_gdf = gdf[gdf["type"] == "road"]

    if road_gdf.empty:
        empty = GeometryCollection()
        return {
            "raw_geom": empty,
            "feature": {
                "type": "Feature",
                "geometry": empty.__geo_interface__,
                "properties": {
                    "type": "main_road",
                    "source": "DXF",
                    "status": "not_present"
                }
            }
        }

    road_geom = unary_union(road_gdf.geometry)

    feature = {
        "type": "Feature",
        "geometry": road_geom.__geo_interface__,
        "properties": {
            "type": "main_road",
            "source": "DXF",
            "status": "locked"
        }
    }

    return {
        "raw_geom": road_geom,
        "feature": feature
    }
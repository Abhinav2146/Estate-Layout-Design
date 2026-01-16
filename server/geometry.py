import geopandas as gpd
import os
import json
from shapely.ops import unary_union
from shapely.geometry import GeometryCollection


# ------------------------------------------------------------
# INTERNAL: Load GeoJSON
# ------------------------------------------------------------
def _load_geojson(project_id, data_dir):
    path = os.path.join(data_dir, f"{project_id}_map.geojson")
    if not os.path.exists(path):
        raise FileNotFoundError("GeoJSON not found for project")
    return gpd.read_file(path)


def _load_constraints(project_id, config_dir):
    path = os.path.join(config_dir, f"{project_id}_constraints.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


# ------------------------------------------------------------
# BUILDABLE AREA
# ------------------------------------------------------------
def generate_buildable_area(project_id, data_dir, config_dir):
    """
    Buildable Area =
      (boundary - boundary_setback)
      - buffer(obstacle)
      - buffer(DXF road)
    """

    gdf = _load_geojson(project_id, data_dir)
    constraints = _load_constraints(project_id, config_dir)

    boundary_setback = float(constraints.get("setback_boundary_m", 5))
    obstacle_buffer = float(constraints.get("buffer_obstacle_m", 3))

    boundary_gdf = gdf[gdf["type"] == "boundary"]
    obstacle_gdf = gdf[gdf["type"] == "obstacle"]
    road_gdf = gdf[gdf["type"] == "road"]

    if boundary_gdf.empty:
        raise ValueError("DXF must contain a boundary polygon")

    # --------------------------------------------------
    # Boundary setback (INWARD)
    # --------------------------------------------------
    site_geom = unary_union(boundary_gdf.geometry)
    gross_area = site_geom.area

    if boundary_setback > 0:
        site_geom = site_geom.buffer(-boundary_setback)

    subtract_geoms = []

    # --------------------------------------------------
    # Obstacle buffer
    # --------------------------------------------------
    if not obstacle_gdf.empty:
        obs_geom = unary_union(obstacle_gdf.geometry)
        if obstacle_buffer > 0:
            obs_geom = obs_geom.buffer(obstacle_buffer)
        subtract_geoms.append(obs_geom)

    # --------------------------------------------------
    # Road buffer (use same obstacle buffer unless specified)
    # --------------------------------------------------
    if not road_gdf.empty:
        road_geom = unary_union(road_gdf.geometry)
        road_geom = road_geom.buffer(obstacle_buffer)
        subtract_geoms.append(road_geom)

    # --------------------------------------------------
    # Final buildable
    # --------------------------------------------------
    if subtract_geoms:
        buildable_geom = site_geom.difference(unary_union(subtract_geoms))
    else:
        buildable_geom = site_geom

    buildable_geom = buildable_geom.buffer(0)  # clean geometry
    usable_area = buildable_geom.area

    feature = {
        "type": "Feature",
        "geometry": buildable_geom.__geo_interface__,
        "properties": {
            "type": "buildable_area",
            "boundary_setback_m": boundary_setback,
            "obstacle_buffer_m": obstacle_buffer
        }
    }

    # 🔒 RETURN TYPE MAINTAINED
    return {
        "raw_geom": buildable_geom,
        "metrics": { 
            "gross_area_sqm": gross_area,
            "usable_area_sqm": usable_area
        },
        "feature": feature
    }


# ------------------------------------------------------------
# MAIN ROAD (DXF ONLY)
# ------------------------------------------------------------
def generate_main_road(project_id, data_dir, config_dir, site_geom):
    """
    Main road is read ONLY from DXF.
    No procedural generation.
    """

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

    # 🔒 RETURN TYPE MAINTAINED
    return {
        "raw_geom": road_geom,
        "feature": feature
    }

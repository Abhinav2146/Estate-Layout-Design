from shapely.geometry import shape, LineString, Point
from shapely.ops import unary_union
import json
import os
import math

def generate_buildable_area(project_id: str, data_dir: str, config_dir: str):
    # Load the Land GeoJSON
    geojson_path = os.path.join(data_dir, f"{project_id}_map.geojson")
    with open(geojson_path, "r") as f:
        land_data = json.load(f)

    # Load the Planning Constraints
    config_path = os.path.join(config_dir, f"{project_id}_constraints.json")
    with open(config_path, "r") as f:
        constraints = json.load(f)

    # Extract geometries from GeoJSON
    boundary_geom = None
    obstacle_geoms = []

    for feature in land_data["features"]:
        geom = shape(feature["geometry"])
        if feature["properties"].get("type") == "boundary":
            boundary_geom = geom
        elif feature["properties"].get("type") == "obstacle":
            obstacle_geoms.append(geom)

    if not boundary_geom:
        raise ValueError("Site boundary not found in GeoJSON")
    
    gross_area_sqm = boundary_geom.area

    # Apply Setback Buffer (Negative buffer to shrink inward) 
    setback_m = constraints.get("setback_boundary_m", 5.0)
    buildable_area = boundary_geom.buffer(-setback_m)

    # Apply Obstacle Buffers 
    # Subtract obstacles + their required buffer from the buildable area
    obstacle_buffer_m = constraints.get("buffer_obstacle_m", 3.0)
    if obstacle_geoms:
        # Combine all obstacles into one shape and buffer them
        obstacles_union = unary_union([obs.buffer(obstacle_buffer_m) for obs in obstacle_geoms])
        # Subtract from our buildable zone
        buildable_area = buildable_area.difference(obstacles_union)

    usable_area_sqm = buildable_area.area

    return {
        "feature": {
            "type": "Feature",
            "properties": {
                "type": "buildable_area",
                "label": "Buildable Footprint",
                "style": {"fill": "#00ff00", "opacity": 0.3}
            },
            "geometry": buildable_area
        },
        "metrics": {
            "gross_area_sqm": round(gross_area_sqm, 2),
            "gross_area_rai": round(gross_area_sqm/1600, 2),
            "usable_area_sqm": round(usable_area_sqm, 2),
            "usable_area_rai": round(usable_area_sqm/1600, 2)
        },
        "raw_geom": buildable_area
    }

def generate_main_road(project_id, data_dir, config_dir, buildable_area_geom):
    # 1. Load Entry Points
    geojson_path = os.path.join(data_dir, f"{project_id}_map.geojson")
    with open(geojson_path, "r") as f:
        land_data = json.load(f)
    
    entry_points = [shape(f["geometry"]) for f in land_data["features"] if f["properties"].get("type") == "entry_point"]
    
    if not entry_points:
        raise ValueError("No entry points found.")

    # 2. Load Constraints
    config_path = os.path.join(config_dir, f"{project_id}_constraints.json")
    with open(config_path, "r") as f:
        constraints = json.load(f)
    
    road_width = constraints.get("main_road_width_m", 12.0)

    # 3. Generate "Infinite" Spine Road
    # We start at the entry, go to centroid, AND THEN KEEP GOING.
    start_pt = entry_points[0].centroid
    target_pt = buildable_area_geom.centroid
    
    # Vector Math: Calculate direction (dx, dy)
    dx = target_pt.x - start_pt.x
    dy = target_pt.y - start_pt.y
    dist = math.sqrt(dx**2 + dy**2)
    
    if dist == 0:
        extended_end = target_pt
    else:
        # Scale the vector to be long enough to cross the whole site (e.g., 3km)
        scale_factor = 3000.0 / dist
        new_x = start_pt.x + (dx * scale_factor)
        new_y = start_pt.y + (dy * scale_factor)
        extended_end = Point(new_x, new_y)

    # Create the extended line
    full_road_line = LineString([start_pt, extended_end])
    
    # Clip the road so it fits exactly inside the boundary
    # We buffer the boundary slightly to ensure the road reaches the very edge
    road_line_clipped = full_road_line.intersection(buildable_area_geom.buffer(10))

    # Buffer to create width (Polygon)
    road_polygon_raw = road_line_clipped.buffer(road_width / 2, cap_style=2)
    
    # Final clean clip to buildable area
    road_polygon = road_polygon_raw.intersection(buildable_area_geom)
    
    return {
        "feature": {
            "type": "Feature",
            "properties": {
                "type": "road", 
                "label": "Main Access Road",
                "style": {"fill": "#333333", "opacity": 1.0}
            },
            "geometry": road_polygon
        },
        "raw_geom": road_polygon,
        "width": road_width
    }

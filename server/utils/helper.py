import os
import json
from shapely.geometry import Point
import geopandas as gpd
import math
from shapely.strtree import STRtree

DATA_DIR = "data"

def load_entry_points(project_id: str):
    path = os.path.join(DATA_DIR, f"{project_id}_map.geojson")
    if not os.path.exists(path):
        raise FileNotFoundError("GeoJSON not found – upload step missing")

    with open(path) as f:
        geo = json.load(f)

    entry_points = []

    for feat in geo["features"]:
        if feat["properties"].get("type") == "entry_point":
            geom = feat["geometry"]

            # GeoJSON → Shapely Point
            if geom["type"] == "Point":
                x, y = geom["coordinates"]
                entry_points.append(Point(x, y))

    return entry_points

def load_elevation_points(project_id, data_dir):
    path = os.path.join(data_dir, f"{project_id}_map.geojson")
    gdf = gpd.read_file(path)

    elev_row = gdf[gdf["type"] == "elevation"]
    if elev_row.empty:
        return []

    geom = elev_row.iloc[0].geometry

    return [
        (float(pt.x), float(pt.y), float(pt.z))
        for pt in geom.geoms
        if pt.has_z
    ]

# ------------------------------------------------------------
# Helper: Get Highway Angle
# ------------------------------------------------------------
def get_dominant_angle(geometry):
    if geometry is None or geometry.is_empty: return 0.0
    if geometry.geom_type in ['Polygon', 'MultiPolygon']: geometry = geometry.boundary
    
    lines = list(geometry.geoms) if geometry.geom_type == 'MultiLineString' else [geometry]
    longest_len = 0
    angle = 0

    for line in lines:
        coords = list(line.coords)
        for i in range(len(coords) - 1):
            dx = coords[i+1][0] - coords[i][0]
            dy = coords[i+1][1] - coords[i][1]
            length = math.sqrt(dx**2 + dy**2)
            if length > longest_len:
                longest_len = length
                angle = math.degrees(math.atan2(dy, dx))
    return angle

# ------------------------------------------------------------
# Elevation Sampler (FIXED for Shapely 2.0+)
# ------------------------------------------------------------
def build_elevation_index(elevation_points):
    # Create points for the spatial index
    pts = [Point(x, y) for x, y, z in elevation_points]
    # Store Z values in a simple list corresponding to the points order
    z_values = [z for x, y, z in elevation_points]
    
    # Return tree and the list of Z values (instead of a map)
    return STRtree(pts), z_values

def parcel_elevation(parcel_geom, tree, z_values, samples=7):
    """
    Returns minimum elevation inside parcel (best for drainage logic)
    """
    minx, miny, maxx, maxy = parcel_geom.bounds

    sample_pts = [
        parcel_geom.centroid,
        Point(minx, miny),
        Point(minx, maxy),
        Point(maxx, miny),
        Point(maxx, maxy)
    ]

    elevations = []

    for sp in sample_pts:
        # Shapely 2.0+: tree.nearest returns the INT INDEX of the nearest geometry
        nearest_idx = tree.nearest(sp)
        
        # Ensure we have a valid index
        if nearest_idx is not None:
            # In case nearest_idx is a numpy scalar, convert to int
            idx = int(nearest_idx)
            if 0 <= idx < len(z_values):
                elevations.append(z_values[idx])

    return min(elevations) if elevations else 0.0

def get_elevation_stats(geom, tree, z_values):
    """
    Returns (min_z, max_z, variance) for points within or near the geometry.
    Used for 3.4.1 Slope Analysis.
    """
    if tree is None: return 0.0, 0.0, 0.0

    minx, miny, maxx, maxy = geom.bounds
    # Sample points: Corners + Center
    samples = [geom.centroid, Point(minx, miny), Point(minx, maxy), Point(maxx, miny), Point(maxx, maxy)]
    
    elevs = []
    for p in samples:
        # Shapely 2.0+: returns integer index
        nearest_idx = tree.nearest(p)
        if nearest_idx is not None:
            # Ensure index is int
            idx = int(nearest_idx)
            if 0 <= idx < len(z_values):
                elevs.append(z_values[idx])
    
    if not elevs: return 0.0, 0.0, 0.0
    
    min_z = min(elevs)
    max_z = max(elevs)
    variance = max_z - min_z # Simple delta approximation for slope
    return min_z, max_z, variance
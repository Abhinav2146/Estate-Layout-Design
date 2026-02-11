import os
import json
from shapely.geometry import Point, Polygon, MultiPolygon
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

def create_entry_splay(line_geom, entry_pt, width):
        """Creates a 45-degree chamfer/splay at the end of the line closest to entry_pt."""
        if line_geom is None or line_geom.is_empty: return Polygon()
        
        # --- FIX START: Handle MultiLineString ---
        actual_line = line_geom
        if line_geom.geom_type == 'MultiLineString':
            # Pick the line segment that is actually closest to the entry point
            # (The intersection might have split the road into multiple parts)
            if not line_geom.geoms: return Polygon()
            actual_line = min(line_geom.geoms, key=lambda g: g.distance(entry_pt))
        elif line_geom.geom_type == 'GeometryCollection':
             # Try to find a LineString in the collection
             lines = [g for g in line_geom.geoms if g.geom_type == 'LineString']
             if lines:
                 actual_line = min(lines, key=lambda g: g.distance(entry_pt))
             else:
                 return Polygon()
        # --- FIX END ---
        
        if actual_line.geom_type != 'LineString': return Polygon()
        
        # Determine which end is the start (closest to entry point)
        coords = list(actual_line.coords)
        if len(coords) < 2: return Polygon()

        p_start, p_end = coords[0], coords[-1]
        dist_start = Point(p_start).distance(entry_pt)
        dist_end = Point(p_end).distance(entry_pt)
        
        if dist_start < dist_end:
            p1, p2 = coords[0], coords[1]
        else:
            p1, p2 = coords[-1], coords[-2]
            
        # Vector P1 -> P2 (Road direction into the site)
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length = math.sqrt(dx*dx + dy*dy)
        if length == 0: return Polygon()
        ux, uy = dx/length, dy/length
        
        # Right vector (Perpendicular)
        rx, ry = -uy, ux
        
        # Splay size (Leg length of the 45 deg triangle)
        splay_len = width 
        
        # Calculate Base Corners at P1 (The standard road corners)
        # Left Corner
        lc_x = p1[0] - (width/2)*rx
        lc_y = p1[1] - (width/2)*ry
        
        # Right Corner
        rc_x = p1[0] + (width/2)*rx
        rc_y = p1[1] + (width/2)*ry
        
        # Generate 45-degree Triangles
        # Left Triangle: From Left Corner, go Out (Left) and Forward (Up road)
        lt_p2 = (lc_x - splay_len*rx, lc_y - splay_len*ry) # Out
        lt_p3 = (lc_x + splay_len*ux, lc_y + splay_len*uy) # Forward
        left_tri = Polygon([(lc_x, lc_y), lt_p2, lt_p3])
        
        # Right Triangle: From Right Corner, go Out (Right) and Forward (Up road)
        rt_p2 = (rc_x + splay_len*rx, rc_y + splay_len*ry) # Out
        rt_p3 = (rc_x + splay_len*ux, rc_y + splay_len*uy) # Forward
        right_tri = Polygon([(rc_x, rc_y), rt_p2, rt_p3])
        
        return MultiPolygon([left_tri, right_tri])
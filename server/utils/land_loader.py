import ezdxf
import geopandas as gpd
from shapely.geometry import Polygon, LineString, Point, MultiPoint
from shapely.ops import unary_union
from typing import Dict, Any, List
import numpy as np

def dxf_to_geojson(dxf_path: str, output_geojson: str = None) -> Dict[str, Any]:
    # Read DXF
    try:
        doc = ezdxf.readfile(dxf_path)
    except IOError:
        raise ValueError(f"Not a DXF file or a generic I/O error: {dxf_path}")
    except ezdxf.DXFStructureError:
        raise ValueError(f"Invalid or corrupt DXF file: {dxf_path}")

    msp = doc.modelspace()

    # --- CONSTANTS ---
    ROAD_LAYERS = {"Road", "3-PHASE-ROAD", "EG_Road"}
    
    # UPDATED: Removed CONTOUR layers from ignore list
    IGNORE_LAYERS = {
        "0", "Defpoints"
    }
    
    # Specific layers that contain elevation data
    CONTOUR_LAYERS = {
        "CONTOUR", "INDEX-CON", 
        "HR36 IE-Exi_Xref$0$CONTOUR", "HR36 IE-Exi_Xref$0$CONTOURINDEX"
    }

    PRIMARY_ENTRY_BLOCKS = {"main-entrance"} 
    
    # --- DATA CONTAINERS ---
    roads = []
    entry_points = []
    candidate_polygons = [] 
    
    # New: Elevation Data Points (x, y, z)
    elevation_points: List[tuple] = []

    for e in msp:
        etype = e.dxftype()
        layer = e.dxf.layer

        # 1. Capture Entry Points
        if etype == "POINT" and layer in PRIMARY_ENTRY_BLOCKS:
            p = e.dxf.location
            entry_points.append(Point(p.x, p.y))
        
        # 2. Capture Elevation Data (Contours & Points)
        if layer in CONTOUR_LAYERS:
            if etype == "LWPOLYLINE":
                # LWPolylines are 2D but have a single 'elevation' attribute
                z_val = e.dxf.elevation
                # Collect vertices with this Z value
                with e.points("xy") as points:
                    for x, y in points:
                        elevation_points.append((x, y, z_val))
                        
            elif etype == "POLYLINE":
                # POLYLINE can be 2D or 3D
                if e.is_2d_polyline:
                    z_val = e.dxf.elevation if e.dxf.hasattr("elevation") else 0.0
                    for v in e.vertices:
                        elevation_points.append((v.dxf.location.x, v.dxf.location.y, z_val))
                else:
                    # 3D Polyline: Z is on every vertex
                    for v in e.vertices:
                        loc = v.dxf.location
                        elevation_points.append((loc.x, loc.y, loc.z))
                        
            elif etype == "LINE":
                start = e.dxf.start
                end = e.dxf.end
                elevation_points.append((start.x, start.y, start.z))
                elevation_points.append((end.x, end.y, end.z))
                
            elif etype == "POINT":
                loc = e.dxf.location
                elevation_points.append((loc.x, loc.y, loc.z))
            
            # Skip adding contours to geometry processing (roads/parcels)
            continue

        # 3. Capture Roads
        if layer in ROAD_LAYERS:
            if etype == "LINE":
                roads.append(LineString([e.dxf.start, e.dxf.end]))
            elif etype in ("LWPOLYLINE", "POLYLINE"):
                pts = [(p[0], p[1]) for p in e.get_points()]
                if len(pts) >= 2:
                    roads.append(LineString(pts))
            continue 

        # 4. Capture Polygons (Boundaries / Obstacles)
        if etype in ("LWPOLYLINE", "POLYLINE"):
            if layer in IGNORE_LAYERS or layer in ROAD_LAYERS:
                continue

            points = [(p[0], p[1]) for p in e.get_points()]
            if len(points) < 3:
                continue
            
            if points[0] != points[-1]:
                points.append(points[0])
            
            try:
                poly = Polygon(points)
                if not poly.is_valid:
                    continue
                
                if layer == "36Bound": 
                    candidate_polygons.append(poly)
                else:
                    # Future: Handle internal obstacles here
                    pass 
            except Exception:
                continue

    # --- PROCESS GEOMETRY ---
    obstacles = [] 
    if candidate_polygons:
        site_boundary = unary_union(candidate_polygons)
    else:
        site_boundary = Polygon()

    is_valid = not site_boundary.is_empty and site_boundary.is_valid

    # --- PROCESS ELEVATION STATISTICS ---
    min_elev = 0.0
    max_elev = 0.0
    avg_elev = 0.0
    
    # We create a simple 'terrain_mesh' to save for later analysis
    # For now, we just return statistics required by API-01
    if elevation_points:
        z_values = [p[2] for p in elevation_points]
        min_elev = float(np.min(z_values))
        max_elev = float(np.max(z_values))
        avg_elev = float(np.mean(z_values))
    
    # Save the raw elevation points to a separate file for the Subdivision step to use later?
    # For this MVP, we will bundle them into the GeoJSON output as a MultiPoint feature
    # so the frontend (or next API step) can visualize/use them.

    # --- OUTPUT CONSTRUCTION ---
    features = []
    
    if is_valid:
        features.append({
            "geometry": site_boundary, 
            "type": "boundary", 
            "label": "Total Site Extent"
        })
    
    for obs in obstacles:
        features.append({
            "geometry": obs, 
            "type": "obstacle", 
            "label": "Potential Obstacle/Structure"
        })

    for rd in roads:
        features.append({"geometry": rd, "type": "road", "label": "Existing Road"})
        
    for ep in entry_points:
        features.append({"geometry": ep, "type": "entry_point", "label": "Main Station/Access"})
        
# Add elevation points as MultiPoint Z (CORRECT WAY)
# ------------------------------------------------------------
    if elevation_points:
        elevation_geom = MultiPoint([
            (float(x), float(y), float(z))
            for x, y, z in elevation_points
        ])

        features.append({
            "geometry": elevation_geom,
            "type": "elevation",
            "label": "Terrain Elevation"
        })

    
    if features:
        gdf = gpd.GeoDataFrame(features)
        gdf.set_crs("EPSG:32647", inplace=True) 
        if output_geojson:
            gdf.to_file(output_geojson, driver="GeoJSON")

    return {
        "geometry_valid": is_valid and len(entry_points)>0,
        "area_sqm": round(site_boundary.area, 2),
        "area_rai": round(site_boundary.area / 1600, 2),
        "entry_point_count": len(entry_points),
        "obstacle_count": len(obstacles),
        "road_segment_count": len(roads),
        # NEW METRICS FOR API-01
        "elevation_min": round(min_elev, 2),
        "elevation_max": round(max_elev, 2),
        "elevation_avg": round(avg_elev, 2),
        "elevation_points_count": len(elevation_points)
    }
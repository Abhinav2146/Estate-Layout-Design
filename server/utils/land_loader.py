import ezdxf
import geopandas as gpd
from shapely.geometry import Polygon, LineString, Point, MultiPolygon
from shapely.ops import unary_union
from typing import Dict, Any

def dxf_to_geojson(dxf_path: str, output_geojson: str = None) -> Dict[str, Any]:
    # Read DXF
    try:
        doc = ezdxf.readfile(dxf_path)
    except IOError:
        raise ValueError(f"Not a DXF file or a generic I/O error: {dxf_path}")
    except ezdxf.DXFStructureError:
        raise ValueError(f"Invalid or corrupt DXF file: {dxf_path}")

    msp = doc.modelspace()

    # Define Layer & Block Constants
    ROAD_LAYERS = {"Road", "3-PHASE-ROAD", "EG_Road"}
    
    # Layers to IGNORE (Contours/Topography) - These create false obstacles
    IGNORE_LAYERS = {
        "CONTOUR", "INDEX-CON", 
        "HR36 IE-Exi_Xref$0$CONTOUR", "HR36 IE-Exi_Xref$0$CONTOURINDEX",
        "0", "Defpoints"
    }
    
    # Entry Points (Strictly 'STA')
    PRIMARY_ENTRY_BLOCKS = {"STA"} 
    
    # Data Containers
    roads = []
    entry_points = []
    candidate_polygons = [] # All valid closed shapes (Plots + Buildings)

    for e in msp:
        etype = e.dxftype()
        layer = e.dxf.layer

        # Capture Entry Points
        if etype == "INSERT" and e.dxf.name in PRIMARY_ENTRY_BLOCKS:
            entry_points.append(Point(e.dxf.insert.x, e.dxf.insert.y))
        
        # Capture Roads
        if layer in ROAD_LAYERS:
            if etype == "LINE":
                roads.append(LineString([e.dxf.start, e.dxf.end]))
            elif etype in ("LWPOLYLINE", "POLYLINE"):
                pts = [(p[0], p[1]) for p in e.get_points()]
                if len(pts) >= 2:
                    roads.append(LineString(pts))
            continue 

        # Capture Polygons (Candidates for Land OR Obstacles)
        if etype in ("LWPOLYLINE", "POLYLINE"):
            if layer in IGNORE_LAYERS or layer in ROAD_LAYERS:
                continue

            points = [(p[0], p[1]) for p in e.get_points()]
            if len(points) < 3:
                continue
            
            # Ensure the loop is closed
            if points[0] != points[-1]:
                points.append(points[0])
            
            try:
                poly = Polygon(points)
                if poly.is_valid:
                    candidate_polygons.append(poly)
            except Exception:
                continue

    # Process Logic: Distinguish Land from Obstacles
    if candidate_polygons:
        # Calculate Total Site Area (Union of EVERYTHING)
        site_boundary = unary_union(candidate_polygons)
        
        # Identify Obstacles
        # Sort polygons by area to find the "Main Plot"
        sorted_polys = sorted(candidate_polygons, key=lambda p: p.area, reverse=True)
        largest_area = sorted_polys[0].area
        
        # HEURISTIC: Anything smaller than 1% of the largest plot is an "Obstacle"
        # (e.g., Buildings, Ponds, reserved areas inside the main plot)
        obstacles = [p for p in sorted_polys if p.area < (largest_area * 0.01)]
    else:
        site_boundary = Polygon()
        obstacles = []

    # Generate Validation & Metrics
    is_valid = not site_boundary.is_empty and site_boundary.is_valid

    # Generate GeoJSON Output
    features = []
    
    # Site Boundary Feature
    if is_valid:
        features.append({
            "geometry": site_boundary, 
            "type": "boundary", 
            "label": "Total Site Extent"
        })
    
    # Obstacle Features
    for obs in obstacles:
        features.append({
            "geometry": obs, 
            "type": "obstacle", 
            "label": "Potential Obstacle/Structure"
        })

    # Road Features
    for rd in roads:
        features.append({"geometry": rd, "type": "road", "label": "Existing Road"})
        
    # Entry Point Features
    for ep in entry_points:
        features.append({"geometry": ep, "type": "entry_point", "label": "Main Station/Access"})

    if features:
        gdf = gpd.GeoDataFrame(features)
        gdf.set_crs("EPSG:32647", inplace=True) # UTM Zone 47N
        if output_geojson:
            gdf.to_file(output_geojson, driver="GeoJSON")

    return {
        "geometry_valid": is_valid and len(entry_points)>0,
        "area_sqm": round(site_boundary.area, 2),
        "area_rai": round(site_boundary.area / 1600, 2),
        "entry_point_count": len(entry_points),
        "obstacle_count": len(obstacles),
        "road_segment_count": len(roads)
    }
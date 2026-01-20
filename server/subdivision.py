from shapely.geometry import box, Polygon, MultiPolygon, LineString, Point, MultiLineString, shape
from shapely.ops import unary_union
from shapely.affinity import rotate, translate
from shapely.prepared import prep
import json
import os
import math
import random

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
# MAIN FUNCTION
# ------------------------------------------------------------
def generate_parcels(project_id, data_dir, config_dir, buildable_geom, road_geom, road_config=None):

    if buildable_geom is None or buildable_geom.is_empty: return []
    
    # --- 0. DIRECT BUILDABLE GEOMETRY (No Obstacle Subtraction) ---
    # User confirmed input is already buildable, so we skip obstacle checks.
    effective_buildable = buildable_geom.buffer(0)

    if effective_buildable.is_empty:
        return []

    # --- 1. CONFIG & ROADS ---
    config_path = os.path.join(config_dir, f"{project_id}_constraints.json")
    with open(config_path, "r") as f: constraints = json.load(f)
    
    parcel_program = constraints.get("parcel_program", [])
    min_green_ratio = constraints.get("min_green_ratio", 0.1) 
    
    # Recalculate total site area based on the actual effective buildable
    total_site_area = effective_buildable.area

    total_prog_weight = sum(p.get("target_percent", 0) for p in parcel_program)
    if total_prog_weight > 0:
        avg_area = sum(((p["min_area"] + p["max_area"])/2) * p.get("target_percent", 0) for p in parcel_program) / total_prog_weight
    else:
        avg_area = 500 

    est_depth = math.sqrt(avg_area * 1.5) 
    
    if road_config:
        main_road_width = road_config.get("main_road_width", 12.0)
        local_road_width = road_config.get("local_road_width", 8.0)
        vertical_spacing = road_config.get("vertical_spacing", (est_depth * 2) + local_road_width)
        horizontal_spacing = road_config.get("horizontal_spacing", (est_depth * 4) + local_road_width)
    else:
        main_road_width = constraints.get("main_road_width_m", 12.0)
        local_road_width = constraints.get("local_road_width_m", 8.0)
        vertical_spacing = (est_depth * 2) + local_road_width
        horizontal_spacing = (est_depth * 5) + local_road_width

    features = []

    # --- 2. GENERATE ROTATED GRID ---
    minx, miny, maxx, maxy = effective_buildable.bounds
    center_x, center_y = (minx + maxx) / 2, (miny + maxy) / 2
    
    # We still use road_geom for ALIGNMENT, but we won't draw it.
    grid_rotation = get_dominant_angle(road_geom) if road_geom else 0
    
    diag = math.sqrt((maxx - minx)**2 + (maxy - miny)**2)
    extent = diag * 1.5
    
    lines_v, lines_h, junction_points = [], [], []
    
    curr = -extent/2
    while curr < extent/2:
        lines_v.append(LineString([(curr, -extent/2), (curr, extent/2)]))
        curr += vertical_spacing
    
    curr = -extent/2
    while curr < extent/2:
        lines_h.append(LineString([(-extent/2, curr), (extent/2, curr)]))
        curr += horizontal_spacing

    vx = -extent/2
    while vx < extent/2:
        vy = -extent/2
        while vy < extent / 2:
            junction_points.append(Point(vx, vy))
            vy += horizontal_spacing
        vx += vertical_spacing

    def transform(g): return translate(rotate(g, grid_rotation, origin=(0,0)), xoff=center_x, yoff=center_y)
    
    # --- ROAD GENERATION ---
    final_h = transform(MultiLineString(lines_h)).buffer(main_road_width/2, cap_style=1, join_style=1).buffer(0)
    final_v = transform(MultiLineString(lines_v)).buffer(local_road_width/2, cap_style=1, join_style=1).buffer(0)
    
    # Junctions (Reduced size to prevent overlapping purple roads)
    final_j = transform(MultiPolygon([p.buffer(main_road_width*0.55) for p in junction_points])).buffer(0)
    
    internal_main_roads = unary_union([final_h, final_j]).buffer(0)
    internal_local_roads = final_v

    # --- DISABLED EXTERNAL HIGHWAY GENERATION ---
    # We force this to be empty so the existing highway is NOT drawn/subtracted.
    external_road_poly = Polygon()

    all_main_logic = unary_union([internal_main_roads, external_road_poly]).buffer(0)
    full_road_network_raw = unary_union([all_main_logic, internal_local_roads]).buffer(0)
    
    full_road_network = full_road_network_raw.intersection(effective_buildable).buffer(0)
    
    remaining_land = effective_buildable.difference(full_road_network).buffer(0)
    
    if remaining_land.is_empty: return []

    # --- 2.2 OUTPUT SEPARATE ROAD FEATURES ---
    
    # Main Roads
    # Only internal main roads will be generated
    internal_main_vis = internal_main_roads.intersection(effective_buildable).buffer(0)
    
    if not internal_main_vis.is_empty:
        m_geoms = internal_main_vis.geoms if internal_main_vis.geom_type == 'MultiPolygon' else [internal_main_vis]
        for mg in m_geoms:
            if mg.area < 0.1: continue
            features.append({
                "type": "Feature", 
                "geometry": mg, 
                "properties": {"type": "road", "road_type": "main", "area_sqm": round(mg.area, 2)}
            })
    
    # Local Roads
    # Buffer 0 added aggressively to fix "missing" purple roads
    local_export = internal_local_roads.intersection(effective_buildable).buffer(0)
    final_local_export = local_export.difference(internal_main_vis).buffer(0)

    if not final_local_export.is_empty:
        l_geoms = final_local_export.geoms if final_local_export.geom_type == 'MultiPolygon' else [final_local_export]
        for lg in l_geoms:
            # Removed area filter to ensure all road segments are drawn
            if lg.is_empty or lg.geom_type not in ['Polygon', 'MultiPolygon']: continue 
            features.append({
                "type": "Feature", 
                "geometry": lg, 
                "properties": {"type": "road", "road_type": "local", "area_sqm": round(lg.area, 2)}
            })

    # --- 3. ALLOCATE PARCELS ---
    
    total_area = remaining_land.area
    for p in parcel_program:
        avg = (p["min_area"] + p["max_area"]) / 2
        p["target_count"] = max(1, int((total_area * p["target_percent"]) / avg))
        p["allocated_count"] = 0

    parcel_program.sort(key=lambda x: x["min_area"], reverse=True)

    cx, cy = effective_buildable.centroid.x, effective_buildable.centroid.y
    aligned_land = rotate(remaining_land, -grid_rotation, origin=(cx, cy))
    
    road_buffer_world = full_road_network.buffer(0.5) 
    aligned_road_zone = rotate(road_buffer_world, -grid_rotation, origin=(cx, cy))
    prep_road_zone = prep(aligned_road_zone)

    blocks = list(aligned_land.geoms) if aligned_land.geom_type == "MultiPolygon" else [aligned_land]
    parcel_geoms_local = [] 

    # Phase 1
    for block in blocks:
        if block.area < 100: continue
        lbminx, lbminy, lbmaxx, lbmaxy = block.bounds
        prep_block = prep(block)

        curr_y = lbminy
        while curr_y < lbmaxy:
            active_prog = next((p for p in parcel_program if p["allocated_count"] < p["target_count"]), None)
            if not active_prog: active_prog = parcel_program[-1] 
            
            min_a, max_a = active_prog["min_area"], active_prog["max_area"]
            avg_w_d_ratio = 1.5 
            row_depth = math.sqrt(((min_a + max_a)/2) * avg_w_d_ratio) * 0.8
            
            curr_x = lbminx
            while curr_x < lbmaxx:
                attempt_success = False
                target_areas = [max_a, (min_a+max_a)/2, min_a]
                for target_area in target_areas:
                    width = target_area / row_depth
                    candidate = box(curr_x, curr_y, curr_x + width, curr_y + row_depth)
                    if not prep_block.intersects(candidate): continue
                    clipped = candidate.intersection(block)
                    if clipped.is_empty or clipped.geom_type != 'Polygon' or len(clipped.interiors) > 0 or clipped.area < (min_a * 0.7):
                        continue
                    if not prep_road_zone.intersects(clipped): continue

                    parcel_geoms_local.append({
                        "geom": clipped,
                        "props": {"type": "parcel", "size_group": active_prog["size_group"], "area_sqm": round(clipped.area, 2)}
                    })
                    active_prog["allocated_count"] += 1
                    curr_x += width 
                    attempt_success = True
                    break 
                if not attempt_success: curr_x += 0.5 
            curr_y += row_depth

    # Phase 2
    if parcel_geoms_local:
        existing_local_union = unary_union([p["geom"] for p in parcel_geoms_local])
        leftover_local = aligned_land.difference(existing_local_union).buffer(-0.1)
    else:
        leftover_local = aligned_land

    if not leftover_local.is_empty:
        leftover_blocks = list(leftover_local.geoms) if leftover_local.geom_type == "MultiPolygon" else [leftover_local]
        fill_prog = parcel_program[-1]
        min_a = fill_prog["min_area"]
        fill_depth = math.sqrt(min_a * 1.5) * 0.8
        
        for l_block in leftover_blocks:
            if l_block.area < min_a: continue 
            b_minx, b_miny, b_maxx, b_maxy = l_block.bounds
            prep_l_block = prep(l_block)
            cy_fill = b_miny
            while cy_fill < b_maxy:
                cx_fill = b_minx
                while cx_fill < b_maxx:
                    width = min_a / fill_depth
                    candidate = box(cx_fill, cy_fill, cx_fill + width, cy_fill + fill_depth)
                    if prep_l_block.intersects(candidate):
                        clipped = candidate.intersection(l_block)
                        if not clipped.is_empty and clipped.geom_type == 'Polygon' and len(clipped.interiors) == 0 and clipped.area >= (min_a * 0.8):
                             if prep_road_zone.intersects(clipped):
                                parcel_geoms_local.append({
                                    "geom": clipped,
                                    "props": {"type": "parcel", "size_group": fill_prog["size_group"], "area_sqm": round(clipped.area, 2)}
                                })
                                cx_fill += width 
                                continue
                    cx_fill += 1.0 
                cy_fill += fill_depth

    # --- 4. ENFORCE GREEN RATIO ---
    final_parcels = []
    current_parcel_area = 0
    for p_data in parcel_geoms_local:
        world_geom = rotate(p_data["geom"], grid_rotation, origin=(cx, cy))
        p_data["world_geom"] = world_geom
        final_parcels.append(p_data)
        current_parcel_area += world_geom.area

    total_built_area = full_road_network.area + current_parcel_area
    current_green_area = total_site_area - total_built_area
    current_ratio = current_green_area / total_site_area

    if current_ratio < min_green_ratio:
        while current_ratio < min_green_ratio and final_parcels:
            removed = final_parcels.pop()
            current_parcel_area -= removed["world_geom"].area
            total_built_area = full_road_network.area + current_parcel_area
            current_green_area = total_site_area - total_built_area
            current_ratio = current_green_area / total_site_area
    
    for p_data in final_parcels:
        features.append({
            "type": "Feature", "geometry": p_data["world_geom"], "properties": p_data["props"]
        })

    # --- 5. GREEN SPACE ---
    # Simplified Green Space calculation (No obstacle union)
    if final_parcels:
        built_parcels = unary_union([p["world_geom"] for p in final_parcels])
        total_built = unary_union([built_parcels, full_road_network])
        final_green = effective_buildable.difference(total_built).buffer(0)
    else:
        final_green = effective_buildable.difference(full_road_network).buffer(0)

    if not final_green.is_empty:
        greens = list(final_green.geoms) if final_green.geom_type == "MultiPolygon" else [final_green]
        for g in greens:
            features.append({
                "type": "Feature", "geometry": g, "properties": {"type": "green", "area_sqm": round(g.area, 2)}
            })

    return features
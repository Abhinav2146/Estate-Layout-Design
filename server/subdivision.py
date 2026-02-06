from shapely.geometry import box, Polygon, MultiPolygon, LineString, Point
from shapely.ops import unary_union
from shapely.affinity import rotate, translate
from shapely.prepared import prep
import json
import os
import math
from utils.helper import get_dominant_angle, get_elevation_stats, build_elevation_index, parcel_elevation

def generate_parcels(project_id, data_dir, config_dir, buildable_geom, road_geom, entry_points, elevation_points, road_config=None):

    if buildable_geom is None or buildable_geom.is_empty: return []
    
    effective_buildable = buildable_geom.buffer(0)
    if effective_buildable.is_empty: return []

    # --- 1. CONFIG & ROADS ---
    config_path = os.path.join(config_dir, f"{project_id}_constraints.json")
    with open(config_path, "r") as f: constraints = json.load(f)
    
    parcel_program = constraints.get("parcel_program", [])
    min_green_ratio = constraints.get("min_green_ratio", 0.1) 
    
    total_site_area = effective_buildable.area
    
    # Calculate weighted average area for road spacing estimation
    total_prog_weight = sum(p.get("target_percent", 0) for p in parcel_program)
    if total_prog_weight > 0:
        avg_area = sum(((p["min_area"] + p["max_area"])/2) * p.get("target_percent", 0) for p in parcel_program) / total_prog_weight
    else:
        avg_area = 500 

    # Estimate depth based on aspect ratio 1.5
    est_depth = math.sqrt(avg_area * 1.5) 
    
    if road_config:
        main_road_width = road_config.get("main_road_width", 12.0)
        local_road_width = road_config.get("local_road_width", 8.0)
        vertical_spacing = road_config.get("vertical_spacing", (est_depth * 2) + local_road_width)
        horizontal_spacing = road_config.get("horizontal_spacing", (est_depth * 500) + main_road_width)
    else:
        main_road_width = constraints.get("main_road_width_m", 12.0)
        local_road_width = constraints.get("local_road_width_m", 8.0)
        vertical_spacing = (est_depth * 2) + local_road_width
        horizontal_spacing = (est_depth * 500) + main_road_width

    features = []

    # --- 2. GENERATE ROTATED GRID ---
    minx, miny, maxx, maxy = effective_buildable.bounds
    center_x, center_y = (minx + maxx) / 2, (miny + maxy) / 2
    
    grid_rotation = get_dominant_angle(road_geom) if road_geom else 0
    
    diag = math.sqrt((maxx - minx)**2 + (maxy - miny)**2)
    extent = diag * 2.0
    
    # Store ABSTRACT lines (before transformation)
    abstract_lines_v = []
    abstract_lines_h = []
    junction_points = []
    
    curr = -extent/2
    while curr < extent/2:
        abstract_lines_v.append(LineString([(curr, -extent/2), (curr, extent/2)]))
        curr += vertical_spacing
    
    curr = -extent/2
    while curr < extent/2:
        abstract_lines_h.append(LineString([(-extent/2, curr), (extent/2, curr)]))
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
    
    # 1. Local Roads (Vertical)
    valid_vertical_centerlines = []
    inset_site = effective_buildable.buffer(-15.0) 
    if inset_site.is_empty: inset_site = effective_buildable.buffer(-5.0)

    final_v_polys = []
    for line in abstract_lines_v:
        world_line = transform(line)
        if world_line.intersects(inset_site):
            clipped_center = world_line.intersection(effective_buildable)
            if not clipped_center.is_empty:
                valid_vertical_centerlines.append(clipped_center)
                final_v_polys.append(clipped_center.buffer(local_road_width/2, cap_style=1, join_style=1))
    
    internal_local_roads = unary_union(final_v_polys).buffer(0)
    
    # 2. Entry Main Roads
    entry_road_polys = []
    entry_road_centerlines = []

    if entry_points and len(entry_points) > 0:
        grid_rad = math.radians(grid_rotation)
        candidate_angles = [grid_rad, grid_rad + math.pi/2] 
        
        selected_lines = [] 
        for entry_pt in entry_points:
            ep_x, ep_y = entry_pt.x, entry_pt.y
            best_line = None
            max_len = -1
            
            for angle_rad in candidate_angles:
                dx = math.cos(angle_rad) * extent
                dy = math.sin(angle_rad) * extent
                line = LineString([(ep_x - dx, ep_y - dy), (ep_x + dx, ep_y + dy)])
                
                clipped = line.intersection(effective_buildable)
                if not clipped.is_empty:
                    curr_len = clipped.length
                    if curr_len > max_len:
                        max_len = curr_len
                        best_line = line 

            if best_line:
                selected_lines.append({"origin": entry_pt, "line": best_line})

        if len(selected_lines) == 2:
            l1, l2 = selected_lines[0], selected_lines[1]
            intersection = l1["line"].intersection(l2["line"])
            if not intersection.is_empty and intersection.geom_type == 'Point':
                seg1 = LineString([(l1["origin"].x, l1["origin"].y), (intersection.x, intersection.y)]).intersection(effective_buildable)
                seg2 = LineString([(l2["origin"].x, l2["origin"].y), (intersection.x, intersection.y)]).intersection(effective_buildable)
                if not seg1.is_empty: 
                    entry_road_polys.append(seg1.buffer(main_road_width/2, cap_style=1, join_style=1))
                    entry_road_centerlines.append(seg1)
                if not seg2.is_empty: 
                    entry_road_polys.append(seg2.buffer(main_road_width/2, cap_style=1, join_style=1))
                    entry_road_centerlines.append(seg2)
            else:
                for item in selected_lines:
                    clipped = item["line"].intersection(effective_buildable)
                    if not clipped.is_empty: 
                        entry_road_polys.append(clipped.buffer(main_road_width/2, cap_style=1, join_style=1))
                        entry_road_centerlines.append(clipped)
        else:
            for item in selected_lines:
                clipped = item["line"].intersection(effective_buildable)
                if not clipped.is_empty: 
                    entry_road_polys.append(clipped.buffer(main_road_width/2, cap_style=1, join_style=1))
                    entry_road_centerlines.append(clipped)

    entry_main_roads = unary_union(entry_road_polys) if entry_road_polys else Polygon()

    # 3. Horizontal Main Roads
    all_anchors = []
    if valid_vertical_centerlines: all_anchors.extend(valid_vertical_centerlines)
    if entry_road_centerlines: all_anchors.extend(entry_road_centerlines)
    
    horizontal_road_polys = []
    
    if all_anchors:
        anchors_geom = unary_union(all_anchors)
        for h_line in abstract_lines_h:
            world_h_line = transform(h_line)
            intersection = world_h_line.intersection(anchors_geom)
            
            if intersection.is_empty: continue
            points = []
            if intersection.geom_type == 'Point': points.append(intersection)
            elif intersection.geom_type == 'MultiPoint': points.extend(list(intersection.geoms))
            elif intersection.geom_type in ['LineString', 'MultiLineString', 'GeometryCollection']:
                geoms = intersection.geoms if intersection.geom_type == 'GeometryCollection' else ([intersection] if intersection.geom_type == 'LineString' else list(intersection.geoms))
                for g in geoms:
                    if g.geom_type == 'Point': points.append(g)
                    elif g.geom_type == 'LineString':
                        points.append(Point(g.coords[0]))
                        points.append(Point(g.coords[-1]))

            if len(points) < 2: continue
            sorted_points = sorted(points, key=lambda p: world_h_line.project(p))
            if sorted_points[0].distance(sorted_points[-1]) < 5.0: continue
            
            trimmed_segment = LineString([sorted_points[0], sorted_points[-1]])
            if trimmed_segment.intersects(effective_buildable):
                horizontal_road_polys.append(trimmed_segment.buffer(main_road_width/2, cap_style=1, join_style=1))

    final_h = unary_union(horizontal_road_polys).buffer(0) if horizontal_road_polys else Polygon()
    final_j = transform(MultiPolygon([p.buffer(main_road_width*0.55) for p in junction_points])).buffer(0).intersection(effective_buildable)
    
    internal_main_roads = unary_union([final_h, final_j, entry_main_roads]).buffer(0)
    full_road_network = unary_union([internal_main_roads, internal_local_roads]).intersection(effective_buildable).buffer(0)
    
    remaining_land = effective_buildable.difference(full_road_network).buffer(0)
    if remaining_land.is_empty: return []

    # --- OUTPUT ROAD FEATURES ---
    internal_main_vis = internal_main_roads.intersection(effective_buildable).buffer(0)
    if not internal_main_vis.is_empty:
        m_geoms = internal_main_vis.geoms if internal_main_vis.geom_type == 'MultiPolygon' else [internal_main_vis]
        for mg in m_geoms:
            if mg.area < 0.1: continue
            features.append({
                "type": "Feature", "geometry": mg, "properties": {"type": "road", "road_type": "main", "area_sqm": round(mg.area, 2)}
            })
    
    local_export = internal_local_roads.intersection(effective_buildable).buffer(0)
    final_local_export = local_export.difference(internal_main_vis).buffer(0)
    if not final_local_export.is_empty:
        l_geoms = final_local_export.geoms if final_local_export.geom_type == 'MultiPolygon' else [final_local_export]
        for lg in l_geoms:
            if lg.area < 0.1: continue
            features.append({
                "type": "Feature", "geometry": lg, "properties": {"type": "road", "road_type": "local", "area_sqm": round(lg.area, 2)}
            })

    # --- 3. ALLOCATE PARCELS (Adaptive Greedy Strategy) ---
    total_area = remaining_land.area
    for p in parcel_program:
        avg = (p["min_area"] + p["max_area"]) / 2
        p["target_count"] = max(1, int((total_area * p["target_percent"]) / avg))
        p["allocated_count"] = 0
    parcel_program.sort(key=lambda x: x["min_area"], reverse=True)

    cx, cy = effective_buildable.centroid.x, effective_buildable.centroid.y
    aligned_land = rotate(remaining_land, -grid_rotation, origin=(cx, cy))
    # Prep road zone for access checks (using small buffer to ensure contact)
    road_access_zone = full_road_network.buffer(1.0) 
    prep_road_zone = prep(rotate(road_access_zone, -grid_rotation, origin=(cx, cy)))
    
    blocks = list(aligned_land.geoms) if aligned_land.geom_type == "MultiPolygon" else [aligned_land]
    parcel_geoms_local = [] 

    # --- PHASE 1: Greedy Grid with Expansion ---
    for block in blocks:
        if block.area < 50: continue # Skip tiny fragments
        lbminx, lbminy, lbmaxx, lbmaxy = block.bounds
        prep_block = prep(block)
        
        curr_y = lbminy
        min_dim_threshold = 5.0 # Minimum dimension to be considered "usable" on its own

        while curr_y < lbmaxy:
            # 1. Select Program
            active_prog = next((p for p in parcel_program if p["allocated_count"] < p["target_count"]), None)
            if not active_prog: active_prog = parcel_program[-1] 
            
            min_a, max_a = active_prog["min_area"], active_prog["max_area"]
            
            # 2. Determine Row Height (Depth)
            # Try to aim for a depth that supports the max area first
            # Assuming aspect ratio ~1.5 for max area: Depth = sqrt(max_a * 1.5) * 0.8 to be safe? 
            # Let's standardise depth slightly based on average to keep rows somewhat neat, 
            # but bias towards LARGER depth to maximize area.
            row_depth = math.sqrt(max_a * 1.5) * 0.8
            
            # 3. Vertical Extension (Lookahead)
            # If the space remaining ABOVE this row is useless (< min_dim), eat it.
            remaining_y = lbmaxy - (curr_y + row_depth)
            if 0 < remaining_y < min_dim_threshold:
                row_depth += remaining_y
            
            # Ensure we don't exceed block bounds
            if curr_y + row_depth > lbmaxy:
                row_depth = lbmaxy - curr_y

            curr_x = lbminx
            while curr_x < lbmaxx:
                # 4. Determine Parcel Width
                # Try to fit MAX area first
                target_width = max_a / row_depth
                
                # 5. Horizontal Extension (Lookahead)
                # If space remaining to the RIGHT is useless, eat it.
                remaining_x = lbmaxx - (curr_x + target_width)
                if 0 < remaining_x < min_dim_threshold:
                    target_width += remaining_x
                
                # Candidate box
                candidate = box(curr_x, curr_y, curr_x + target_width, curr_y + row_depth)
                
                # Intersection & Validation
                if prep_block.intersects(candidate):
                    clipped = candidate.intersection(block)
                    
                    # Basic geometry checks
                    if not clipped.is_empty and clipped.geom_type == 'Polygon' and len(clipped.interiors) == 0:
                        
                        # Area Checks
                        # We allow slightly over max_a (e.g. 10%) if it helps fill a void, 
                        # but strictly enforce min_a
                        area = clipped.area
                        if area >= (min_a * 0.9): # 10% tolerance on min
                            
                            # Road Access Check
                            if prep_road_zone.intersects(clipped):
                                
                                # ACCEPT PARCEL
                                parcel_geoms_local.append({
                                    "geom": clipped, 
                                    "props": {
                                        "type": "parcel", 
                                        "size_group": active_prog["size_group"], 
                                        "area_sqm": round(area, 2)
                                    }
                                })
                                active_prog["allocated_count"] += 1
                                curr_x += target_width
                                continue # Move to next slot
                
                # If Max failed, try Min dimensions to see if something smaller fits?
                # For "Maximize Saleable", we usually just advance. 
                # Retrying with smaller widths often results in slivers. 
                # Instead, we advance by a smaller step to try a new alignment?
                # No, standard grid logic implies advancing by width. 
                # If we failed, it might be due to boundary shape.
                # Let's try a smaller fallback width (Min Area) just in case.
                
                min_width = min_a / row_depth
                candidate_min = box(curr_x, curr_y, curr_x + min_width, curr_y + row_depth)
                if prep_block.intersects(candidate_min):
                     clipped_min = candidate_min.intersection(block)
                     if not clipped_min.is_empty and clipped_min.area >= (min_a * 0.9) and prep_road_zone.intersects(clipped_min):
                          parcel_geoms_local.append({
                                    "geom": clipped_min, 
                                    "props": {
                                        "type": "parcel", 
                                        "size_group": active_prog["size_group"], 
                                        "area_sqm": round(clipped_min.area, 2)
                                    }
                                })
                          active_prog["allocated_count"] += 1
                          curr_x += min_width
                          continue

                # If both failed, step forward small amount to find valid land
                curr_x += 1.0 
            
            curr_y += row_depth

    # --- PHASE 2: Fill Voids (Aggressive) ---
    if parcel_geoms_local:
        existing_parcels_geom = unary_union([p["geom"] for p in parcel_geoms_local])
        leftover_local = aligned_land.difference(existing_parcels_geom).buffer(-0.1)
    else:
        leftover_local = aligned_land

    if not leftover_local.is_empty:
        leftover_blocks = list(leftover_local.geoms) if leftover_local.geom_type == "MultiPolygon" else [leftover_local]
        # Use the smallest program to fill gaps
        fill_prog = parcel_program[-1]
        min_a = fill_prog["min_area"]
        
        for l_block in leftover_blocks:
            if l_block.area < min_a: continue 
            
            b_minx, b_miny, b_maxx, b_maxy = l_block.bounds
            prep_l_block = prep(l_block)
            
            # Simple scan for fill
            fill_depth = math.sqrt(min_a * 1.5) * 0.8
            cy_fill = b_miny
            while cy_fill < b_maxy:
                cx_fill = b_minx
                while cx_fill < b_maxx:
                    width = min_a / fill_depth
                    candidate = box(cx_fill, cy_fill, cx_fill + width, cy_fill + fill_depth)
                    
                    if prep_l_block.intersects(candidate):
                        clipped = candidate.intersection(l_block)
                        if not clipped.is_empty and clipped.area >= (min_a * 0.8):
                             if prep_road_zone.intersects(clipped):
                                parcel_geoms_local.append({
                                    "geom": clipped, 
                                    "props": {
                                        "type": "parcel", 
                                        "size_group": fill_prog["size_group"], 
                                        "area_sqm": round(clipped.area, 2)
                                    }
                                })
                                cx_fill += width 
                                continue
                    cx_fill += 1.0 
                cy_fill += fill_depth

    final_parcels = []
    for p_data in parcel_geoms_local:
        world_geom = rotate(p_data["geom"], grid_rotation, origin=(cx, cy))
        p_data["world_geom"] = world_geom
        final_parcels.append(p_data)

# ... (Previous code remains the same up to Section 5) ...

    # --- 5. ASSIGN UTILITIES (WTP & PONDS) ---
    # 5A. REAL ELEVATION ASSIGNMENT
    # ------------------------------------------------------------
    if elevation_points:
        elev_tree, z_values = build_elevation_index(elevation_points)

        for p in final_parcels:
            p["elevation_real"] = parcel_elevation(
                p["world_geom"],
                elev_tree,
                z_values                                              
            )
    else:
        # Absolute fallback (should never happen now)
        for p in final_parcels:
            p["elevation_real"] = 0.0


    # 5B. Sort: Lowest Elevation First
    final_parcels.sort(key=lambda x: x["elevation_real"])

    # --- NEW: DEFINE EXCLUSION ZONE (Away from Main Roads) ---
    # We want to avoid placing utilities right next to the main boulevard.
    # Collect all main road geometries (Existing + Generated)
    main_road_geoms = []
    if not road_geom.is_empty: main_road_geoms.append(road_geom)
    if not entry_main_roads.is_empty: main_road_geoms.append(entry_main_roads)
    if not internal_main_roads.is_empty: main_road_geoms.append(internal_main_roads)
    
    exclusion_zone = Polygon()
    if main_road_geoms:
        # Buffer distance: How far away should utilities be? (e.g., 40 meters)
        utility_avoid_distance = constraints.get("utility_road_separation_m", 40.0)
        all_main_roads = unary_union(main_road_geoms)
        exclusion_zone = all_main_roads.buffer(utility_avoid_distance)

    # Helper to check if a parcel is "too close" to main road
    def is_prime_area(parcel_geom):
        if exclusion_zone.is_empty: return False
        return parcel_geom.intersects(exclusion_zone)

    # --- 5C. Targets ---
    total_generated_area = sum(p["world_geom"].area for p in final_parcels)
    target_wtp = total_generated_area * constraints.get("wtp_target_percent", 0.02)
    target_pond = total_generated_area * constraints.get("retention_pond_target_percent", 0.07)

    # --- 6. SEED & GROW ALGORITHM FOR WTP (With Exclusion Logic) ---
    wtp_indices = set()
    current_wtp_area = 0
    
    if final_parcels:
        # 1. Find the Best Seed (Lowest Elevation AND Not in Prime Area)
        seed_idx = -1
        
        # Pass 1: Strict Check (Must be away from road)
        for i, p in enumerate(final_parcels):
            if not is_prime_area(p["world_geom"]):
                seed_idx = i
                break
        
        # Pass 2: Fallback (If site is small/narrow, pick lowest regardless)
        if seed_idx == -1:
            seed_idx = 0 
        
        wtp_indices.add(seed_idx)
        current_wtp_area += final_parcels[seed_idx]["world_geom"].area
        
        # 2. Grow Contiguous Area
        while current_wtp_area < target_wtp:
            current_geoms = [final_parcels[i]["world_geom"] for i in wtp_indices]
            current_union = unary_union(current_geoms)
            best_candidate = -1
            
            # We prefer neighbors that are also NOT in prime area, but continuity is priority
            candidates = []
            for i, p in enumerate(final_parcels):
                if i in wtp_indices: continue
                # Check adjacency (buffer slight amount to ensure touch)
                if p["world_geom"].buffer(0.5).intersects(current_union):
                    candidates.append(i)
            
            if not candidates: break

            # Sort candidates: Prefer Non-Prime first, then by Elevation (original index)
            # Since 'final_parcels' is already sorted by elevation, we just need to prioritize Non-Prime.
            candidates.sort(key=lambda idx: (1 if is_prime_area(final_parcels[idx]["world_geom"]) else 0, idx))
            
            best_candidate = candidates[0]
            wtp_indices.add(best_candidate)
            current_wtp_area += final_parcels[best_candidate]["world_geom"].area

    # Generate WTP Feature
    wtp_geoms_to_merge = []
    for idx in wtp_indices:
        p = final_parcels[idx]
        p["props"]["label"] = "WTP" 
        wtp_geoms_to_merge.append(p["world_geom"])

    if wtp_geoms_to_merge:
        wtp_union = unary_union(wtp_geoms_to_merge)
        features.append({
            "type": "Feature",
            "geometry": wtp_union,
            "properties": {
                "type": "green",
                "subtype": "utility",
                "label": "WTP",
                "utility_type": "Water Treatment Plant",
                "area_sqm": wtp_union.area
            }
        })

    # --- 7. ASSIGN PONDS (Distributed + Setbacks + Exclusion) ---
    current_pond_area = 0
    pond_setback = constraints.get("pond_setback_m", 10.0)
    pond_indices = set()
    
    # Pass 1: Best Candidates (Lowest + Distributed + AWAY FROM ROAD)
    for i, p in enumerate(final_parcels):
        if i in wtp_indices: continue 
        if current_pond_area >= target_pond: break
        
        # EXCLUSION CHECK: Skip if near main road
        if is_prime_area(p["world_geom"]): continue
        
        # DISTRIBUTION CHECK: Skip if touching existing pond
        is_touching_existing = False
        current_poly = p["world_geom"]
        for existing_idx in pond_indices:
            existing_poly = final_parcels[existing_idx]["world_geom"]
            if current_poly.distance(existing_poly) < 1.0:
                is_touching_existing = True
                break
        
        if not is_touching_existing:
            pond_indices.add(i)
            current_pond_area += current_poly.area

    # Pass 2: Fill Gaps (Relax Distribution, Keep Exclusion)
    if current_pond_area < target_pond:
        for i, p in enumerate(final_parcels):
            if i in wtp_indices or i in pond_indices: continue
            if current_pond_area >= target_pond: break
            
            if is_prime_area(p["world_geom"]): continue
            
            pond_indices.add(i)
            current_pond_area += p["world_geom"].area

    # Pass 3: Desperation (Relax Exclusion - if we still need area)
    if current_pond_area < target_pond:
        for i, p in enumerate(final_parcels):
            if i in wtp_indices or i in pond_indices: continue
            if current_pond_area >= target_pond: break
            
            # Now we accept parcels near main road if we have no choice
            pond_indices.add(i)
            current_pond_area += p["world_geom"].area

    # Generate Pond Features (with Setbacks)
# ---------------------------------------------------------
    # MODIFIED: Generate Pond Features (Water + Green Bank)
    # ---------------------------------------------------------
    for i, p in enumerate(final_parcels):
        if i in wtp_indices: continue 
        
        if i in pond_indices:
            # 1. Calculate the Water Surface (Inner)
            water_surface = p["world_geom"].buffer(-pond_setback)
            
            if water_surface.is_empty:
                # Fallback: Parcel too small for setback, make it all green park
                p["props"].update({"type": "green", "label": "Green Space", "subtype": "park"})
                features.append({
                    "type": "Feature", 
                    "geometry": p["world_geom"], 
                    "properties": p["props"]
                })
            else:
                # 2. Calculate the Bank (Outer Ring)
                # We take the full parcel and subtract the water surface
                bank_geom = p["world_geom"].difference(water_surface)
                
                # 3. Add Water Feature (Blue)
                # formatting props for the water
                water_props = p["props"].copy()
                water_props.update({
                    "type": "green",
                    "subtype": "utility",
                    "label": "Retention Pond",     # 'Pond' triggers BLUE in export_dxf
                    "utility_type": "Retention Pond",
                    "water_area_sqm": round(water_surface.area, 2)
                })
                features.append({
                    "type": "Feature",
                    "geometry": water_surface,
                    "properties": water_props
                })

                # 4. Add Bank Feature (Green)
                if not bank_geom.is_empty:
                    bank_props = p["props"].copy()
                    bank_props.update({
                        "type": "green",
                        "subtype": "buffer",
                        "label": "Utility Buffer", # AVOID using "Pond" here so it stays GREEN
                        "utility_type": "Buffer",
                        "area_sqm": round(bank_geom.area, 2)
                    })
                    features.append({
                        "type": "Feature",
                        "geometry": bank_geom,
                        "properties": bank_props
                    })
        else:
            # Standard Parcels
            features.append({
                "type": "Feature", 
                "geometry": p["world_geom"], 
                "properties": p["props"]
            })

    # ... (Rest of cleanup logic Section 8) ...

    # --- 8. RESIDUAL GREEN (CLEANUP) ---
    if final_parcels:
        built_parcels = unary_union([p["world_geom"] for p in final_parcels])
        total_built = unary_union([built_parcels, full_road_network])
        final_green = effective_buildable.difference(total_built).buffer(0)
    else:
        final_green = effective_buildable.difference(full_road_network).buffer(0)

    if not final_green.is_empty:
        greens = list(final_green.geoms) if final_green.geom_type == "MultiPolygon" else [final_green]
        for g in greens:
            # High threshold to avoid reporting tiny slivers as parks
            if g.area < 100.0: continue 
            features.append({
                "type": "Feature", "geometry": g, "properties": {"type": "green", "label": "Park", "subtype": "park", "area_sqm": round(g.area, 2)}
            })

    return features
from shapely.geometry import box, Polygon, MultiPolygon, LineString, Point, MultiLineString
from shapely.ops import unary_union
from shapely.affinity import rotate, translate
import json
import os
import math
import random


# ------------------------------------------------------------
# Parcel size sampler (RECTANGULAR, VARIABLE, MIN–MAX SAFE)
# ------------------------------------------------------------
def choose_parcel_dimensions(min_area, max_area, aspect_ratio_range=(1.2, 2.8)):
    """
    Generates realistic rectangular parcels with:
    - area ∈ [min_area, max_area]
    - variable width & depth
    - frontage-friendly aspect ratios
    """
    area = random.triangular(
        min_area,
        max_area,
        (min_area + max_area) / 2
    )

    ratio = random.uniform(*aspect_ratio_range)  # depth / width
    width = math.sqrt(area / ratio)
    depth = width * ratio

    return width, depth

# ------------------------------------------------------------
# Helper: Get Highway Angle
# ------------------------------------------------------------
def get_dominant_angle(geometry):
    """Calculates the orientation of the longest segment in the geometry."""
    if geometry is None or geometry.is_empty:
        return 0.0
    
    # Ensure we are looking at lines
    if geometry.geom_type == 'Polygon' or geometry.geom_type == 'MultiPolygon':
        geometry = geometry.boundary

    lines = []
    if geometry.geom_type == 'LineString':
        lines = [geometry]
    elif geometry.geom_type == 'MultiLineString':
        lines = list(geometry.geoms)
    else:
        return 0.0

    longest_len = 0
    angle = 0

    for line in lines:
        coords = list(line.coords)
        for i in range(len(coords) - 1):
            p1 = coords[i]
            p2 = coords[i+1]
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            length = math.sqrt(dx**2 + dy**2)
            if length > longest_len:
                longest_len = length
                # Calculate angle in degrees
                angle = math.degrees(math.atan2(dy, dx))
    
    return angle

# ------------------------------------------------------------
# MAIN FUNCTION
# ------------------------------------------------------------
def generate_parcels(project_id, data_dir, config_dir, buildable_geom, road_geom, road_config=None):

    if buildable_geom is None or buildable_geom.is_empty:
        return []

    # --------------------------------------------------------
    # Load constraints
    # --------------------------------------------------------
    config_path = os.path.join(config_dir, f"{project_id}_constraints.json")
    with open(config_path, "r") as f:
        constraints = json.load(f)

    parcel_program = constraints.get("parcel_program", [])

    # --------------------------------------------------------
    # Road configuration
    # --------------------------------------------------------
    if road_config:
        main_road_width = road_config.get("main_road_width", 18.0)
        local_road_width = road_config.get("local_road_width", 12.0)
        vertical_spacing = road_config.get("vertical_spacing", 250)
        horizontal_spacing = road_config.get("horizontal_spacing", 180)
    else:
        main_road_width = constraints.get("main_road_width_m", 20.0)
        local_road_width = constraints.get("local_road_width_m", 10.0)
        vertical_spacing = 250
        horizontal_spacing = 180

    FRONTAGE_BUFFER = 25  # meters
    features = []

    # --------------------------------------------------------
    # Build road network (HIGHWAY ALIGNED & CURVED)
    # --------------------------------------------------------
    minx, miny, maxx, maxy = buildable_geom.bounds
    center_x = (minx + maxx) / 2
    center_y = (miny + maxy) / 2
    
    # 1. Determine Grid Orientation
    # We rotate the grid to match the highway, rather than the axis
    grid_rotation = get_dominant_angle(road_geom) if road_geom else 0
    
    # 2. Define Grid Extents (Diagonal to ensure coverage after rotation)
    diag = math.sqrt((maxx - minx)**2 + (maxy - miny)**2)
    extent = diag * 1.2
    
    # 3. Generate Centerlines (in local un-rotated space)
    lines_v = []
    lines_h = []
    junction_points = []
    
    # Generate Vertical Spines
    curr_x = -extent / 2
    while curr_x < extent / 2:
        l = LineString([(curr_x, -extent/2), (curr_x, extent/2)])
        lines_v.append(l)
        curr_x += vertical_spacing

    # Generate Horizontal Ribs
    curr_y = -extent / 2
    while curr_y < extent / 2:
        l = LineString([(-extent/2, curr_y), (extent/2, curr_y)])
        lines_h.append(l)
        curr_y += horizontal_spacing

    # 4. Create Roundabout Nodes at Intersections
    # We do this mathematically to avoid costly geometric intersections later
    # Iterate through the grid coordinates we just generated
    vx = -extent / 2
    while vx < extent / 2:
        vy = -extent / 2
        while vy < extent / 2:
            junction_points.append(Point(vx, vy))
            vy += horizontal_spacing
        vx += vertical_spacing

    # 5. Apply Rotation & Translation (Move grid to site)
    # Combine all logic into lists for affine transformation
    all_lines_v = MultiLineString(lines_v)
    all_lines_h = MultiLineString(lines_h)
    all_junctions = MultiPolygon([p.buffer(main_road_width * 0.8) for p in junction_points]) # Pre-buffer junctions as circles

    # Rotate objects around (0,0) then translate to center of site
    def transform_grid_geom(geom):
        r_geom = rotate(geom, grid_rotation, origin=(0, 0))
        t_geom = translate(r_geom, xoff=center_x, yoff=center_y)
        return t_geom

    final_lines_v = transform_grid_geom(all_lines_v)
    final_lines_h = transform_grid_geom(all_lines_h)
    final_roundabouts = transform_grid_geom(all_junctions)

    # 6. Buffer and Buffer Style (The "Organic" Look)
    # cap_style=1 (Round), join_style=1 (Round) replaces square boxes
    # Vertical (Main) roads
    poly_v = final_lines_v.buffer(main_road_width / 2, cap_style=1, join_style=1)
    # Horizontal (Local) roads
    poly_h = final_lines_h.buffer(local_road_width / 2, cap_style=1, join_style=1)

    # 7. Merge & Clip
    # Combine generated roads + roundabouts + existing highway
    generated_network = unary_union([poly_v, poly_h, final_roundabouts])
    
    # Merge with input highway (DXF) geometry
    if road_geom:
        # Buffer existing road if it's a line to ensure valid polygon union
        if road_geom.geom_type in ['LineString', 'MultiLineString']:
            buffered_highway = road_geom.buffer(main_road_width / 2, cap_style=1, join_style=1)
            full_network_raw = unary_union([generated_network, buffered_highway])
        else:
            full_network_raw = unary_union([generated_network, road_geom])
    else:
        full_network_raw = generated_network

    # Clip to buildable area
    full_road_network = full_network_raw.intersection(buildable_geom).buffer(0)

    remaining_land = buildable_geom.difference(full_road_network).buffer(0)

    if remaining_land.is_empty:
        return []

    # --------------------------------------------------------
    # Add road features
    # --------------------------------------------------------
    road_geoms = (
        list(full_road_network.geoms)
        if full_road_network.geom_type == "MultiPolygon"
        else [full_road_network]
    )

    for r in road_geoms:
        if r.area < 50:
            continue
        
        # Simplified classification based on width/area heuristic 
        # since we don't have the original boxes to check intersection anymore
        circle_factor = (4 * math.pi * r.area) / (r.length ** 2)
        
        if circle_factor > 0.6:
            road_type = "junction" # Roundabout
        elif r.area > (vertical_spacing * main_road_width * 0.5):
            road_type = "main"
        else:
            road_type = "local"

        features.append({
            "type": "Feature",
            "geometry": r,
            "properties": {
                "type": "road",
                "road_type": road_type,
                "area_sqm": round(r.area, 2)
            }
        })

    # --------------------------------------------------------
    # Compute parcel targets (BY AREA)
    # --------------------------------------------------------
    total_buildable_area = remaining_land.area

    parcel_targets = []
    for p in parcel_program:
        avg_area = (p["min_area"] + p["max_area"]) / 2
        target_area = total_buildable_area * p["target_percent"]
        target_count = max(1, int(target_area / avg_area))

        parcel_targets.append({
            **p,
            "target_count": target_count,
            "allocated_count": 0
        })

    # --------------------------------------------------------
    # Allocate parcels (VARIABLE SIZE, FRONTAGE AWARE)
    # --------------------------------------------------------
    for program in parcel_targets:

        min_area = program["min_area"]
        max_area = program["max_area"]
        size_group = program["size_group"]

        blocks = (
            list(remaining_land.geoms)
            if remaining_land.geom_type == "MultiPolygon"
            else [remaining_land]
        )

        for block in blocks:

            if program["allocated_count"] >= program["target_count"]:
                break

            bminx, bminy, bmaxx, bmaxy = block.bounds

            px = bminx
            while px < bmaxx:

                py = bminy
                while py < bmaxy:

                    if program["allocated_count"] >= program["target_count"]:
                        break

                    w, d = choose_parcel_dimensions(min_area, max_area)

                    plot = box(px, py, px + w, py + d)

                    if not plot.within(block):
                        py += d * 0.7
                        continue

                    # frontage constraint for large plots
                    if size_group == "Large":
                        if not plot.intersects(full_road_network.buffer(FRONTAGE_BUFFER)):
                            py += d
                            continue

                    area = plot.area
                    if not (min_area <= area <= max_area):
                        py += d
                        continue

                    features.append({
                        "type": "Feature",
                        "geometry": plot,
                        "properties": {
                            "type": "parcel",
                            "size_group": size_group,
                            "area_sqm": round(area, 2)
                        }
                    })

                    remaining_land = remaining_land.difference(plot)
                    block = block.difference(plot)

                    program["allocated_count"] += 1
                    py += d

                px += w

    # --------------------------------------------------------
    # Remaining land → green / utility
    # --------------------------------------------------------
    if not remaining_land.is_empty:
        features.append({
            "type": "Feature",
            "geometry": remaining_land,
            "properties": {
                "type": "green",
                "area_sqm": round(remaining_land.area, 2)
            }
        })

    return features
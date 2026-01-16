from shapely.geometry import box, Polygon, MultiPolygon, LineString, Point, MultiLineString
from shapely.ops import unary_union
import json
import os
import math
import random

# ------------------------------------------------------------
# GEOMETRY HELPERS (Curved Roads & Smoothing)
# ------------------------------------------------------------
def chaikin_smooth(coords, iterations=3):
    """
    Applies Chaikin's corner cutting algorithm to smooth a polyline.
    Used to turn jagged randomized paths into organic curves.
    """
    if len(coords) < 3:
        return coords

    for _ in range(iterations):
        new_coords = [coords[0]]
        for i in range(len(coords) - 1):
            p0 = coords[i]
            p1 = coords[i+1]
            
            # Create points at 25% and 75% along the segment
            q = (0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1])
            r = (0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1])
            
            new_coords.append(q)
            new_coords.append(r)
        
        new_coords.append(coords[-1])
        coords = new_coords
        
    return coords

def create_curved_connection(p1, p2, bend_factor=0.2):
    """
    Creates a LineString between p1 and p2 with a randomized control point
    to create a curved 'spline-like' geometry.
    """
    # Midpoint
    mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    
    # Vector
    vx, vy = p2[0] - p1[0], p2[1] - p1[1]
    dist = math.sqrt(vx*vx + vy*vy)
    
    # Perpendicular offset (random direction)
    offset = dist * bend_factor * random.uniform(-1, 1)
    
    # Normal vector (-vy, vx) normalized
    if dist == 0: return LineString([p1, p2])
    
    nx, ny = -vy / dist, vx / dist
    cx, cy = mx + nx * offset, my + ny * offset
    
    # Generate raw path [start, control, end] then smooth it
    raw_coords = [p1, (cx, cy), p2]
    smooth_coords = chaikin_smooth(raw_coords, iterations=4)
    
    return LineString(smooth_coords)

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
        # Interpret spacing as average density for organic generation
        avg_spacing = road_config.get("vertical_spacing", 150) 
    else:
        main_road_width = constraints.get("main_road_width_m", 20.0)
        local_road_width = constraints.get("local_road_width_m", 10.0)
        avg_spacing = 150

    FRONTAGE_BUFFER = 25  # meters
    features = []

    # --------------------------------------------------------
    # ORGANIC ROAD GENERATION (Curved + Roundabouts)
    # --------------------------------------------------------
    minx, miny, maxx, maxy = buildable_geom.bounds
    
    # 1. Define Nodes (Perturbed Grid)
    # We use a loose grid as a topological base but heavily perturb positions 
    # to eliminate the "Manhattan" feel.
    
    node_spacing = avg_spacing
    cols = int((maxx - minx) / node_spacing) + 2
    rows = int((maxy - miny) / node_spacing) + 2
    
    nodes = {} # Map (c, r) -> (x, y)
    
    perturbation = node_spacing * 0.4 # allow 40% drift from grid center
    
    for r in range(rows):
        for c in range(cols):
            # Base grid position
            bx = minx + c * node_spacing
            by = miny + r * node_spacing
            
            # Randomized position
            nx = bx + random.uniform(-perturbation, perturbation)
            ny = by + random.uniform(-perturbation, perturbation)
            nodes[(c, r)] = (nx, ny)

    # 2. Generate Connectivity (Edges)
    # Connect nearest topological neighbors to ensure connectivity.
    road_centerlines = []
    junctions = []

    for r in range(rows):
        for c in range(cols):
            curr_node = nodes[(c, r)]
            
            # Connect Horizontal (Right)
            if c < cols - 1:
                right_node = nodes[(c + 1, r)]
                road_centerlines.append(create_curved_connection(curr_node, right_node))
            
            # Connect Vertical (Up)
            if r < rows - 1:
                up_node = nodes[(c, r + 1)]
                road_centerlines.append(create_curved_connection(curr_node, up_node))
                
            # Track junctions for potential roundabouts
            # We filter for nodes reasonably inside the buildable area
            if buildable_geom.contains(Point(curr_node)):
                junctions.append(curr_node)

    # 3. Create Road Surfaces (Buffering)
    # Use shapely styling: cap_style=1 (Round), join_style=1 (Round)
    generated_roads = []
    
    for line in road_centerlines:
        # Check if line is roughly relevant (intersects buildable)
        if line.intersects(buildable_geom):
            # Randomly assign width (Main vs Local)
            # Bias towards Local, use Main for longer segments or random chance
            width = main_road_width if random.random() < 0.2 else local_road_width
            
            poly = line.buffer(width / 2, cap_style=1, join_style=1)
            generated_roads.append(poly)

    # 4. Generate Roundabouts
    # Place roundabouts at random valid junctions (approx 15% density)
    roundabouts = []
    roundabout_radius = main_road_width * 1.2
    
    for j_pt in junctions:
        if random.random() < 0.15:
            rb_poly = Point(j_pt).buffer(roundabout_radius)
            roundabouts.append(rb_poly)

    # 5. Merge & Clean Road Network
    # Combine generated roads, roundabouts, and existing road_geom
    
    # Process existing road_geom (DXF import support)
    existing_roads = []
    if road_geom and not road_geom.is_empty:
        if road_geom.geom_type in ['LineString', 'MultiLineString']:
            # If input is lines (DXF centerlines), buffer them
            existing_roads.append(road_geom.buffer(main_road_width / 2, cap_style=1, join_style=1))
        else:
            # If input is already polygons, keep as is
            existing_roads.append(road_geom)

    # Union all road layers
    all_road_polys = generated_roads + roundabouts + existing_roads
    raw_road_network = unary_union(all_road_polys)
    
    # Clip to buildable area
    full_road_network = raw_road_network.intersection(buildable_geom).buffer(0)
    remaining_land = buildable_geom.difference(full_road_network).buffer(0)

    if remaining_land.is_empty:
        return []

    # --------------------------------------------------------
    # Add road features (Metadata for Output)
    # --------------------------------------------------------
    road_geoms = (
        list(full_road_network.geoms)
        if full_road_network.geom_type == "MultiPolygon"
        else [full_road_network]
    )

    for r in road_geoms:
        if r.area < 10: 
            continue
            
        # Simplified classification for organic roads
        # Use simple area/perimeter heuristic or bounding box
        min_r, min_c, max_r, max_c = r.bounds
        extent = max((max_r - min_r), (max_c - min_c))
        
        r_type = "main" if extent > avg_spacing * 1.5 else "local"

        features.append({
            "type": "Feature",
            "geometry": r,
            "properties": {
                "type": "road",
                "road_type": r_type,
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

                    # frontage constraint: must touch road buffer
                    # Essential for organic layouts where roads aren't predictable
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
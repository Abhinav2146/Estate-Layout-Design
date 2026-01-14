from shapely.geometry import box, Polygon, MultiPolygon
from shapely.ops import unary_union
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
    # Build road network
    # --------------------------------------------------------
    minx, miny, maxx, maxy = buildable_geom.bounds

    vertical_roads = []
    horizontal_roads = []

    x = minx + vertical_spacing
    while x < maxx:
        vertical_roads.append(box(x, miny, x + main_road_width, maxy))
        x += vertical_spacing + main_road_width

    y = miny + horizontal_spacing
    while y < maxy:
        horizontal_roads.append(box(minx, y, maxx, y + local_road_width))
        y += horizontal_spacing + local_road_width

    grid_roads = unary_union(vertical_roads + horizontal_roads)
    full_road_network = unary_union([grid_roads, road_geom]).intersection(buildable_geom)

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

        is_main = any(r.intersects(v) for v in vertical_roads)
        is_local = any(r.intersects(h) for h in horizontal_roads)

        if is_main and not is_local:
            road_type = "main"
        elif is_local and not is_main:
            road_type = "local"
        else:
            bx, by, tx, ty = r.bounds
            road_type = "local" if (tx - bx) > (ty - by) else "main"

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

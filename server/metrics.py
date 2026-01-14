def calculate_metrics(buildable_data, road_data, parcel_features):
    # Initialize stats counters
    stats = {
        "saleable_sqm": 0.0,
        "road_sqm": 0.0,
        "green_sqm": 0.0,
        "plots_total": 0,
        "plots_by_size": {},
        "green_corridors_sqm": 0.0,
        "green_pockets_sqm": 0.0
    }

    # Process Parcel Features
    # These include parcels, local roads, and green areas
    for f in parcel_features:
        props = f["properties"]
        f_type = props.get("type")
        area = props.get("area_sqm", 0.0)

        if f_type == "parcel":
            stats["saleable_sqm"] += area
            stats["plots_total"] += 1
            
            # Count by size group
            group = props.get("size_group", "Unknown")
            stats["plots_by_size"][group] = stats["plots_by_size"].get(group, 0) + 1
            
        elif f_type == "road":
            stats["road_sqm"] += area
            
        elif f_type == "green":
            stats["green_sqm"] += area
            # Track green subtypes for detailed reporting
            subtype = props.get("subtype", "unclassified")
            if subtype == "corridor":
                stats["green_corridors_sqm"] += area
            elif subtype == "pocket":
                stats["green_pockets_sqm"] += area

    # Add Main Road
    # The main road is generated separately, so we add its area now.
    main_road_area = road_data["raw_geom"].area
    stats["road_sqm"] += main_road_area

    # Calculate Totals
    total_site_sqm = buildable_data["metrics"].get("gross_area_sqm", 0)
    total_usable_sqm = buildable_data["metrics"]["usable_area_sqm"]
    
    # Avoid division by zero
    if total_usable_sqm > 0:
        saleable_pct = (stats["saleable_sqm"] / total_usable_sqm) * 100
        road_pct = (stats["road_sqm"] / total_usable_sqm) * 100
        green_pct = (stats["green_sqm"] / total_usable_sqm) * 100
    else:
        saleable_pct = road_pct = green_pct = 0

    # Construct Final JSON Structure
    return {
        "site_analysis": {
            "total_site_sqm": round(total_site_sqm,2),
            "total_site_rai": round(total_site_sqm/1600,2),
            "total_usable_sqm": round(total_usable_sqm, 2),
            "total_usable_rai": round(total_usable_sqm / 1600, 2),
        },
        "land_use_budget": {
            "saleable_area": {
                "sqm": round(stats["saleable_sqm"], 2),
                "rai": round(stats["saleable_sqm"] / 1600, 2),
                "percent": round(saleable_pct, 2)
            },
            "road_area": {
                "sqm": round(stats["road_sqm"], 2),
                "rai": round(stats["road_sqm"] / 1600, 2),
                "percent": round(road_pct, 2)
            },
            "green_area": {
                "sqm": round(stats["green_sqm"], 2),
                "rai": round(stats["green_sqm"] / 1600, 2),
                "percent": round(green_pct, 2),
            }
        },
        "parcel_inventory": {
            "total_plots": stats["plots_total"],
            "breakdown": stats["plots_by_size"]
        }
    }

def calculate_net_buildable_by_size(parcels):
    """
    Returns net buildable statistics per plot size group
    """

    summary = {}
    total_saleable = 0.0

    for f in parcels:
        if f["properties"].get("type") != "parcel":
            continue

        size = f["properties"].get("size_group", "Unknown")
        area = f["properties"].get("area_sqm", 0)

        total_saleable += area

        if size not in summary:
            summary[size] = {
                "plot_count": 0,
                "total_net_buildable_sqm": 0.0
            }

        summary[size]["plot_count"] += 1
        summary[size]["total_net_buildable_sqm"] += area

    # post-process
    for size, data in summary.items():
        count = data["plot_count"]
        area = data["total_net_buildable_sqm"]

        data["avg_plot_size_sqm"] = round(area / count, 2) if count else 0
        data["share_of_saleable_percent"] = (
            round((area / total_saleable) * 100, 1)
            if total_saleable > 0 else 0
        )

        data["total_net_buildable_sqm"] = round(area, 2)

    return {
        "total_saleable_sqm": round(total_saleable, 2),
        "by_size_group": summary
    }

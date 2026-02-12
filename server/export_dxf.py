import ezdxf
import os
import traceback
import json
import math
from ezdxf.enums import TextEntityAlignment

# RGB Color Mapping for AutoCAD - UPDATED FOR HIGH CONTRAST
COLOR_MAP = {
    "BOUNDARY": (220, 220, 220),    # Light Gray
    "GREEN_BELT": (100, 150, 100),  # Darker Green for Setback Ring
    "ROADS_MAIN": (255, 0, 0),      # Red - Main Roads
    "ROADS_LOCAL": (255, 0, 255),   # Magenta (Purple) - Local Roads
    "PARCEL_S": (255, 200, 200),    
    "PARCEL_M": (200, 220, 255),    
    "PARCEL_L": (255, 255, 200),    
    "GREEN_AREA": (150, 200, 150),
    
    # --- UTILITIES ---
    "UTILITY_POND": (0, 100, 255),  # Blue (Retention Pond)
    "UTILITY_WTP": (0, 255, 255),   # Cyan (Water Treatment Plant)

    "PARCEL_BORDER": (50, 50, 50),  
    "TEXT_LABELS": (0, 0, 0),       
    "TABLE_LINES": (100, 100, 100), 
    "TABLE_TEXT": (0, 0, 0)         
}

# --- CONFIGURABLE FONT SIZES ---
TEXT_H_PARCEL = 8.0   
TEXT_H_UTILITY = 8.0  
TEXT_H_TABLE_HEAD = 16.0
TEXT_H_TABLE_BODY = 12

def geometry_to_dxf(project_id, data_dir, buildable_data, road_data, parcel_features, metrics=None, filename=None):
    try:
        # source_dxf = os.path.join(data_dir, f"{project_id}.dxf")
        # if not os.path.exists(source_dxf):
        #     raise FileNotFoundError("Source DXF not found for export")

        # doc = ezdxf.readfile(source_dxf)
        doc = ezdxf.new("R2010") 
        msp = doc.modelspace()

        # INSERT THIS STYLE SETUP (Makes arrows visible) ---
        # Create a specific style for leaders with large arrows
        dim_style_name = "PARCEL_LEADER_STYLE"
        if dim_style_name not in doc.dimstyles:
            style = doc.dimstyles.new(dim_style_name)
            style.dxf.dimasz = 4.0      # Arrow Size (Increased for visibility)
            style.dxf.dimldrblk = ""    # Default closed filled arrow
            style.dxf.dimclrd = 256     # Leader line color (ByLayer)
            style.dxf.dimtxsty = "Standard"

        # Create Layers
        for layer_name in COLOR_MAP.keys():
            if layer_name not in doc.layers:
                doc.layers.new(name=layer_name)

        # --- DRAWING HELPERS ---
        def draw_solid_hatch(geom, layer_name):
            if geom is None or geom.is_empty: return
            
            clean_geom = geom.simplify(0.01, preserve_topology=True)

            if clean_geom.geom_type == 'Polygon':
                try:
                    hatch = msp.add_hatch(dxfattribs={'layer': layer_name})
                    hatch.set_pattern_fill('SOLID')
                    if layer_name in COLOR_MAP:
                        rgb_color = COLOR_MAP[layer_name]
                        hatch.dxf.true_color = ezdxf.colors.rgb2int(rgb_color)
                    
                    hatch.paths.add_polyline_path([(p[0], p[1]) for p in clean_geom.exterior.coords], is_closed=True)
                    for interior in clean_geom.interiors:
                        hatch.paths.add_polyline_path([(p[0], p[1]) for p in interior.coords], is_closed=True)
                except Exception as e:
                    print(f"Hatch Error for {layer_name}: {e}")

            elif clean_geom.geom_type == 'MultiPolygon':
                for poly in clean_geom.geoms:
                    draw_solid_hatch(poly, layer_name)

        def draw_polygon_with_border(geom, fill_layer, border_color=(50, 50, 50), line_width=0.3):
            if geom is None or geom.is_empty: return
            if geom.geom_type == 'Polygon':
                # 1. Draw Fill
                draw_solid_hatch(geom, fill_layer)
                
                # 2. Draw Border
                target_border_layer = fill_layer if "ROADS" in fill_layer else 'PARCEL_BORDER'
                
                exterior_coords = list(geom.exterior.coords)
                polyline = msp.add_lwpolyline(exterior_coords, dxfattribs={'layer': target_border_layer, 'closed': True})
                
                if "ROADS" in fill_layer:
                    if fill_layer in COLOR_MAP:
                         polyline.dxf.true_color = ezdxf.colors.rgb2int(COLOR_MAP[fill_layer])
                else:
                    polyline.dxf.true_color = ezdxf.colors.rgb2int(border_color)
                
                polyline.dxf.lineweight = int(line_width * 100)

            elif geom.geom_type == 'MultiPolygon':
                for poly in geom.geoms:
                    draw_polygon_with_border(poly, fill_layer, border_color, line_width)

        site_center_point = None
        if "raw_geom" in buildable_data and buildable_data["raw_geom"]:
            site_center_point = buildable_data["raw_geom"].centroid
        # --- SMART LABEL HELPER (Auto-Wrap & Fit) ---
        def add_smart_label(msp, text, geom, layer, text_height, site_center=None):
            if not text or not geom or geom.is_empty: return
            
            target_geom = geom
            if geom.geom_type == 'MultiPolygon':
                target_geom = max(geom.geoms, key=lambda g: g.area)

            try:
                # 1. Geometry Analysis
                rect = target_geom.minimum_rotated_rectangle
                coords = list(rect.exterior.coords)
                edge_len_1 = math.hypot(coords[1][0]-coords[0][0], coords[1][1]-coords[0][1])
                edge_len_2 = math.hypot(coords[2][0]-coords[1][0], coords[2][1]-coords[1][1])
                
                if edge_len_1 > edge_len_2:
                    longest_len, short_len = edge_len_1, edge_len_2
                    dx, dy = coords[1][0]-coords[0][0], coords[1][1]-coords[0][1]
                else:
                    longest_len, short_len = edge_len_2, edge_len_1
                    dx, dy = coords[2][0]-coords[1][0], coords[2][1]-coords[1][1]
                
                angle_deg = math.degrees(math.atan2(dy, dx))
                while angle_deg <= -90: angle_deg += 180
                while angle_deg > 90: angle_deg -= 180

                # --- NEW CHECKS ---
                plot_area = target_geom.area
                is_tiny = plot_area < 450.0 
                
                # NARROW CHECK: If width is less than ~2x text height, it's too thin for 2 lines
                # Assuming text_height is ~8.0, we need at least 16-18m width to fit comfortably
                is_narrow = short_len < (text_height * 2.2)

                # Dynamic Settings
                effective_h = text_height * 0.7 if (is_tiny or is_narrow) else text_height
                
                # 2. FIT CHECK
                lines = text.split('\\P')
                max_chars = max([len(line) for line in lines]) if lines else 0
                
                est_text_width = max_chars * effective_h * 0.65 
                est_text_height = len(lines) * effective_h * 1.25
                
                # STRICTER WIDTH CHECK: Only use 80% of width (was 95%) to prevent border touching
                fits_dimensions = (est_text_width <= longest_len * 0.95) and (est_text_height <= short_len * 0.80)
                
                text_area = est_text_width * est_text_height
                area_ratio = text_area / (plot_area + 1e-9)
                aspect_ratio = longest_len / (short_len + 1e-9)
                
                max_fill = 0.90 if is_tiny else 0.80
                
                # FORCE LEADER if it's too narrow, even if the area is big enough
                should_be_inside = fits_dimensions and (area_ratio < max_fill) and not is_narrow

                visual_center = target_geom.representative_point()

                if should_be_inside:
                    # --- PLACEMENT: INSIDE ---
                    
                    mtext = msp.add_mtext(str(text), dxfattribs={
                        'layer': layer,
                        'char_height': effective_h, 
                        'color': 7 
                    })
                    mtext.dxf.width = longest_len 
                    mtext.dxf.rotation = angle_deg
                    mtext.dxf.attachment_point = 5 
                    mtext.dxf.insert = (visual_center.x, visual_center.y)
                    if layer in COLOR_MAP:
                        mtext.dxf.true_color = ezdxf.colors.rgb2int(COLOR_MAP[layer])

                else:
                    # --- PLACEMENT: LEADER ---
                    dir_x, dir_y = 1.0, 1.0 
                    if site_center:
                        dx_c = visual_center.x - site_center.x
                        dy_c = visual_center.y - site_center.y
                        dist = math.hypot(dx_c, dy_c)
                        if dist > 0: dir_x, dir_y = dx_c/dist, dy_c/dist
                    
                    # Offset Logic
                    buffer_dist = 2.0 if (is_tiny or is_narrow) else 12.0
                    offset_dist = (short_len / 2.0) + buffer_dist
                    
                    p_target = (visual_center.x, visual_center.y)
                    p_text = (visual_center.x + dir_x * offset_dist, visual_center.y + dir_y * offset_dist)
                    
                    leader = msp.add_leader(
                        vertices=[p_text, p_target], 
                        dxfattribs={
                            'layer': layer, 
                            'dimstyle': 'PARCEL_LEADER_STYLE'
                        }
                    )
                    
                    if layer in COLOR_MAP: 
                        leader.dxf.true_color = ezdxf.colors.rgb2int(COLOR_MAP[layer])

                    mtext = msp.add_mtext(str(text), dxfattribs={
                        'layer': layer,
                        'char_height': effective_h, 
                        'color': 7
                    })
                    
                    mtext.dxf.attachment_point = 4 if dir_x >= 0 else 6
                    mtext.dxf.insert = p_text      
                    if layer in COLOR_MAP: 
                        mtext.dxf.true_color = ezdxf.colors.rgb2int(COLOR_MAP[layer])

            except Exception as e:
                try:
                    p = target_geom.representative_point()
                    msp.add_text(str(text), dxfattribs={'layer': layer, 'height': text_height})\
                       .set_placement((p.x, p.y), align=TextEntityAlignment.MIDDLE_CENTER)
                except: pass

        # --- DRAW GEOMETRY ---
        
        # 1. Green Belt
        if "green_belt_geom" in buildable_data:
            draw_solid_hatch(buildable_data["green_belt_geom"], "GREEN_BELT")

        # 2. Site Boundary
        if "raw_geom" in buildable_data:
            draw_solid_hatch(buildable_data["raw_geom"], "BOUNDARY")

        for item in parcel_features:
            geom = item.get("geometry")
            props = item.get("properties", {})
            if not geom: continue
            
            f_type = props.get("type")
            
            # --- PARCELS ---
            if f_type == "parcel":
                size = props.get("size_group", "Medium")
                if "Small" in size: layer = "PARCEL_S"
                elif "Large" in size: layer = "PARCEL_L"
                else: layer = "PARCEL_M"
                
                draw_polygon_with_border(geom, layer, border_color=(50, 50, 50), line_width=0.5)
                
                # Label with Area
                label_txt = props.get("label")
                area_val = props.get("area_sqm")
                
                if label_txt:
                    final_label = label_txt
                    if area_val:
                        final_label += f"\\P{int(area_val)} sqm"
                    add_smart_label(msp, final_label, geom, 'TEXT_LABELS', TEXT_H_PARCEL, site_center=site_center_point)

            # --- ROADS ---
            elif f_type == "road":
                road_type = props.get("road_type", "local")
                layer = "ROADS_MAIN" if road_type == "main" else "ROADS_LOCAL"
                l_width = 0.5 if road_type == "main" else 0.4
                draw_polygon_with_border(geom, layer, line_width=l_width)

            # --- UTILITIES & GREEN AREAS (UPDATED) ---
            elif f_type == "green":
                label = props.get("label", "")
                utility = props.get("utility_type", "")
                area_val = props.get("area_sqm")
                subtype = props.get("subtype", "") # Get subtype
                
                # Determine Layer
                if "WTP" in label or "Water Treatment" in utility:
                    target_layer = "UTILITY_WTP"
                elif "Pond" in label or "Retention" in utility:
                    target_layer = "UTILITY_POND"
                else:
                    target_layer = "GREEN_AREA"
                
                draw_polygon_with_border(geom, target_layer, border_color=(50, 100, 50), line_width=0.5)

                # --- LABELING LOGIC ---
                # Determine the base text
                display_label = label if label else utility
                
                # Defaults if empty, BUT SKIP if it is a buffer (we just want area)
                if not display_label and subtype != "buffer":
                    if target_layer == "UTILITY_WTP": display_label = "WTP"
                    elif target_layer == "UTILITY_POND": display_label = "Pond"
                    elif target_layer == "GREEN_AREA": display_label = "Green"

                # Append Area (Handle formatting carefully)
                if area_val:
                    if display_label:
                        display_label += f"\\P{int(area_val)}\u00A0sqm"
                    else:
                        display_label = f"{int(area_val)}\u00A0sqm"

                # Add the label
                add_smart_label(msp, display_label, geom, 'TEXT_LABELS', TEXT_H_UTILITY, site_center=site_center_point)


        # --- DRAW TABLES ---
        if metrics and "raw_geom" in buildable_data:
            try:
                site_bounds = buildable_data["raw_geom"].bounds
                maxx, maxy = site_bounds[2], site_bounds[3]
                x_start = maxx + 50
                y_start = maxy
                
                def draw_row(label, value, y, is_header=False):
                    h = TEXT_H_TABLE_HEAD if is_header else TEXT_H_TABLE_BODY
                    col_width_label = 450
                    col_width_val = 540
                    total_width = col_width_label + col_width_val
                    row_height = 72 if is_header else 54
                    
                    msp.add_line((x_start, y), (x_start + total_width, y), dxfattribs={'layer': 'TABLE_LINES'})
                    msp.add_line((x_start, y), (x_start, y + row_height), dxfattribs={'layer': 'TABLE_LINES'})
                    msp.add_line((x_start + col_width_label, y), (x_start + col_width_label, y + row_height), dxfattribs={'layer': 'TABLE_LINES'})
                    msp.add_line((x_start + total_width, y), (x_start + total_width, y + row_height), dxfattribs={'layer': 'TABLE_LINES'})
                    
                    if label:
                        text = msp.add_text(str(label), dxfattribs={'layer': 'TABLE_TEXT', 'height': h})
                        text.dxf.true_color = ezdxf.colors.rgb2int(COLOR_MAP['TABLE_TEXT'])
                        text.set_placement((x_start + 15, y + row_height/2 - 6), align=TextEntityAlignment.MIDDLE_LEFT)
                    
                    if value is not None and str(value).strip() != "":
                        text = msp.add_text(str(value), dxfattribs={'layer': 'TABLE_TEXT', 'height': h})
                        text.dxf.true_color = ezdxf.colors.rgb2int(COLOR_MAP['TABLE_TEXT'])
                        text.set_placement((x_start + total_width - 15, y + row_height/2 - 6), align=TextEntityAlignment.MIDDLE_RIGHT)

                site = metrics.get("site_analysis", {})
                land = metrics.get("land_use_budget", {})
                inv = metrics.get("parcel_inventory", {})
                breakdown = inv.get("breakdown", {})

                cur_y = y_start
                draw_row("PROJECT SUMMARY", "", cur_y, True)
                cur_y -= 72
                draw_row("Total Site", f"{site.get('total_site_sqm',0):,} sqm", cur_y)
                cur_y -= 54
                
                green_sqm = site.get('green_belt_sqm', 0)
                draw_row("Green Belt Area", f"{green_sqm:,} sqm", cur_y)
                cur_y -= 54

                draw_row("Net Buildable", f"{site.get('total_usable_sqm',0):,} sqm", cur_y)
                cur_y -= 54
                sa = land.get('saleable_area', {})
                draw_row("Saleable Area", f"{sa.get('sqm',0):,} sqm ({sa.get('percent',0)}%)", cur_y)
                cur_y -= 54
                ra = land.get('road_area', {})
                draw_row("Road Area", f"{ra.get('sqm',0):,} sqm ({ra.get('percent',0)}%)", cur_y)
                cur_y -= 54
                ga = land.get('green_area', {})
                draw_row("Internal Green", f"{ga.get('sqm',0):,} sqm ({ga.get('percent',0)}%)", cur_y)
                cur_y -= 54
                draw_row("TOTAL PLOTS", str(inv.get('total_plots',0)), cur_y, True)
                cur_y -= 72
                for k, v in breakdown.items():
                    draw_row(f"{k} Plots", str(v), cur_y)
                    cur_y -= 54
                
                msp.add_line((x_start, cur_y + 54), (x_start + 990, cur_y + 54), dxfattribs={'layer': 'TABLE_LINES'})
                
                # --- DESIGN CONSTRAINTS TABLE ---
                CONFIG_DIR = "config"
                constraints_path = os.path.join(CONFIG_DIR, f"{project_id}_constraints.json")
                constraints = None
                if os.path.exists(constraints_path):
                    with open(constraints_path, "r") as f:
                        constraints = json.load(f)
                
                if constraints:
                    cur_y -= 100 
                    draw_row("DESIGN CONSTRAINTS", "", cur_y, True)
                    cur_y -= 72
                    
                    ind_type = constraints.get('industry_type', 'Light Industry')
                    draw_row("Industry Type", ind_type, cur_y)
                    cur_y -= 54
                    gb_setback = constraints.get('green_belt_setback_m', 10.0)
                    draw_row("Green Belt Setback", f"{gb_setback} m", cur_y)
                    cur_y -= 54
                    pond_tgt = constraints.get('retention_pond_target_percent', 0.07) * 100
                    draw_row("Target: Retention Pond", f"{pond_tgt:.1f}%", cur_y)
                    cur_y -= 54
                    wtp_tgt = constraints.get('wtp_target_percent', 0.02) * 100
                    draw_row("Target: WTP", f"{wtp_tgt:.1f}%", cur_y)
                    cur_y -= 54

                    draw_row("Min Green Ratio", f"{constraints.get('min_green_ratio', 0.0)*100}%", cur_y)
                    cur_y -= 54
                    draw_row("Main Road Width", f"{constraints.get('main_road_width_m', 0)} m", cur_y)
                    cur_y -= 54
                    draw_row("Local Road Width", f"{constraints.get('local_road_width_m', 0)} m", cur_y)
                    cur_y -= 54
                    if "setback_boundary_m" in constraints:
                        draw_row("Boundary Setback", f"{constraints.get('setback_boundary_m')} m", cur_y)
                        cur_y -= 54
                    if "buffer_obstacle_m" in constraints:
                        draw_row("Obstacle Buffer", f"{constraints.get('buffer_obstacle_m')} m", cur_y)
                        cur_y -= 54

                    draw_row("PARCEL PROGRAM", "", cur_y, True)
                    cur_y -= 72
                    
                    for p in constraints.get("parcel_program", []):
                        name = p.get("size_group", "Generic")
                        target = p.get("target_percent", 0) * 100
                        min_a = p.get("min_area", 0)
                        max_a = p.get("max_area", 0)
                        
                        label_str = f"{name} ({min_a:.0f}-{max_a:.0f} sqm)"
                        val_str = f"Target: {target:.0f}%"
                        draw_row(label_str, val_str, cur_y)
                        cur_y -= 54
                        
                    msp.add_line((x_start, cur_y + 54), (x_start + 990, cur_y + 54), dxfattribs={'layer': 'TABLE_LINES'})

            except Exception as e:
                print(f"Warning: Could not draw summary/constraints table: {e}")
                traceback.print_exc()

        if filename is None:
            filename = f"{project_id}_layout.dxf"
        output_path = os.path.join(data_dir, filename)
        doc.saveas(output_path)
        return filename

    except Exception as e:
        print("CRITICAL ERROR in geometry_to_dxf:")
        traceback.print_exc()
        raise e
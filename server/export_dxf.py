import ezdxf
import os
import traceback
from ezdxf.enums import TextEntityAlignment

# RGB Color Mapping for AutoCAD - Industry Standard Lighter Shades
COLOR_MAP = {
    "BOUNDARY": (220, 220, 220),    # Light Gray - Site Boundary
    "ROADS_MAIN": (153, 153, 153),  # Medium Gray - Main Roads
    "ROADS_LOCAL": (153, 153, 153),
    "PARCEL_S": (255, 200, 200),    # Light Red - Small Plots
    "PARCEL_M": (200, 220, 255),    # Light Blue - Medium Plots
    "PARCEL_L": (255, 255, 200),    # Light Yellow - Large Plots
    "GREEN_AREA": (150, 200, 150),  # Medium Green - Green Spaces (darker for visibility)
    "PARCEL_BORDER": (50, 50, 50),  # Dark Gray - Plot Borders
    "TEXT_LABELS": (0, 0, 0),       # Black - Text
    "TABLE_LINES": (100, 100, 100), # Dark Gray - Table Lines
    "TABLE_TEXT": (0, 0, 0)         # Black - Table Text
}

def geometry_to_dxf(project_id, data_dir, buildable_data, road_data, parcel_features, metrics=None, filename=None):
    try:
        # Setup DXF
        doc = ezdxf.new("R2010") 
        msp = doc.modelspace()

        # Define Layers
        for layer_name in COLOR_MAP.keys():
            if layer_name not in doc.layers:
                doc.layers.new(name=layer_name)

        # --- DRAWING HELPERS ---
        def draw_solid_hatch(geom, layer_name):
            if geom is None or geom.is_empty: return
            
            # Handle both Polygon and MultiPolygon
            if geom.geom_type == 'Polygon':
                hatch = msp.add_hatch(dxfattribs={'layer': layer_name})
                hatch.set_pattern_fill('SOLID')
                # Set True Color on hatch entity using proper ezdxf color function
                if layer_name in COLOR_MAP:
                    rgb_color = COLOR_MAP[layer_name]
                    hatch.dxf.true_color = ezdxf.colors.rgb2int(rgb_color)
                
                # Outer loop
                hatch.paths.add_polyline_path([(p[0], p[1]) for p in geom.exterior.coords], is_closed=True)
                # Inner loops (holes)
                for interior in geom.interiors:
                    hatch.paths.add_polyline_path([(p[0], p[1]) for p in interior.coords], is_closed=True)
            elif geom.geom_type == 'MultiPolygon':
                for poly in geom.geoms:
                    draw_solid_hatch(poly, layer_name)

        def draw_lines(geom, layer_name, line_width=0.6):
            if geom is None or geom.is_empty:
                return

            # Handle MultiLineString
            if geom.geom_type == "MultiLineString":
                for line in geom.geoms:
                    draw_lines(line, layer_name, line_width)

            elif geom.geom_type == "LineString":
                pl = msp.add_lwpolyline(
                    list(geom.coords),
                    dxfattribs={"layer": layer_name}
                )
                pl.dxf.lineweight = int(line_width * 100)
                if layer_name in COLOR_MAP:
                    pl.dxf.true_color = ezdxf.colors.rgb2int(COLOR_MAP[layer_name])

        
        def draw_polygon_with_border(geom, fill_layer, border_color=(50, 50, 50), line_width=0.3):
            if geom is None or geom.is_empty: return
            if geom.geom_type == 'Polygon':
                # Draw fill
                draw_solid_hatch(geom, fill_layer)
                # Draw border outline
                exterior_coords = list(geom.exterior.coords)
                polyline = msp.add_lwpolyline(exterior_coords, dxfattribs={'layer': 'PARCEL_BORDER', 'closed': True})
                polyline.dxf.true_color = ezdxf.colors.rgb2int(border_color)
                polyline.dxf.lineweight = int(line_width * 100)  # lineweight in 1/100mm
            elif geom.geom_type == 'MultiPolygon':
                for poly in geom.geoms:
                    draw_polygon_with_border(poly, fill_layer, border_color, line_width)

        # Draw Geometry
        if "raw_geom" in buildable_data:
            draw_solid_hatch(buildable_data["raw_geom"], "BOUNDARY")

        if "raw_geom" in road_data:
            draw_lines(road_data["raw_geom"], "ROADS_MAIN", line_width=0.8)


        for item in parcel_features:
            geom = item.get("geometry")
            props = item.get("properties", {})
            if not geom: continue
            
            f_type = props.get("type")
            if f_type == "parcel":
                size = props.get("size_group", "Medium")
                if "Small" in size: layer = "PARCEL_S"
                elif "Large" in size: layer = "PARCEL_L"
                else: layer = "PARCEL_M"
                # Draw parcel with border for clear differentiation
                draw_polygon_with_border(geom, layer, border_color=(50, 50, 50), line_width=0.5)
                
                # Plot Label
                label_txt = props.get("label")
                if label_txt:
                    center = geom.centroid
                    msp.add_text(str(label_txt), dxfattribs={'layer': 'TEXT_LABELS', 'height': 2.5})\
                       .set_placement((center.x, center.y), align=TextEntityAlignment.MIDDLE_CENTER)

            elif f_type == "road":
                road_type = props.get("road_type", "local")
                layer = "ROADS_MAIN" if road_type == "main" else "ROADS_LOCAL"
                draw_polygon_with_border(geom, layer, border_color=(50, 50, 50), line_width=0.4)
            elif f_type == "green":
                draw_polygon_with_border(geom, "GREEN_AREA", border_color=(50, 100, 50), line_width=0.5)

        # DRAW SUMMARY TABLE
        if metrics and "raw_geom" in buildable_data:
            try:
                # Calculate Table Position
                site_bounds = buildable_data["raw_geom"].bounds
                maxx, maxy = site_bounds[2], site_bounds[3]
                
                x_start = maxx + 50
                y_start = maxy
                
                def draw_row(label, value, y, is_header=False):
                    h = 24.0 if is_header else 16.5
                    col_width_label = 450
                    col_width_val = 540
                    total_width = col_width_label + col_width_val
                    row_height = 72 if is_header else 54
                    
                    # Draw Grid Lines
                    msp.add_line((x_start, y), (x_start + total_width, y), dxfattribs={'layer': 'TABLE_LINES'})
                    msp.add_line((x_start, y), (x_start, y + row_height), dxfattribs={'layer': 'TABLE_LINES'})
                    msp.add_line((x_start + col_width_label, y), (x_start + col_width_label, y + row_height), dxfattribs={'layer': 'TABLE_LINES'})
                    msp.add_line((x_start + total_width, y), (x_start + total_width, y + row_height), dxfattribs={'layer': 'TABLE_LINES'})
                    
                    # Draw Text using Enum Alignment
                    if label:
                        text = msp.add_text(str(label), dxfattribs={'layer': 'TABLE_TEXT', 'height': h})
                        text.dxf.true_color = ezdxf.colors.rgb2int(COLOR_MAP['TABLE_TEXT'])
                        text.set_placement((x_start + 15, y + row_height/2 - 6), align=TextEntityAlignment.MIDDLE_LEFT)
                    
                    if value is not None and str(value).strip() != "":
                        text = msp.add_text(str(value), dxfattribs={'layer': 'TABLE_TEXT', 'height': h})
                        text.dxf.true_color = ezdxf.colors.rgb2int(COLOR_MAP['TABLE_TEXT'])
                        text.set_placement((x_start + total_width - 15, y + row_height/2 - 6), align=TextEntityAlignment.MIDDLE_RIGHT)

                # Extract Data
                site = metrics.get("site_analysis", {})
                land = metrics.get("land_use_budget", {})
                inv = metrics.get("parcel_inventory", {})
                breakdown = inv.get("breakdown", {})

                # Draw Rows
                cur_y = y_start

                draw_row("PROJECT SUMMARY", "", cur_y, True)
                cur_y -= 72
                
                draw_row("Total Site", f"{site.get('total_site_sqm',0):,} sqm", cur_y)
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
                draw_row("Green Area", f"{ga.get('sqm',0):,} sqm ({ga.get('percent',0)}%)", cur_y)
                cur_y -= 54
                
                draw_row("TOTAL PLOTS", str(inv.get('total_plots',0)), cur_y, True)
                cur_y -= 72
                
                for k, v in breakdown.items():
                    draw_row(f"{k} Plots", str(v), cur_y)
                    cur_y -= 54

                # Bottom Line
                msp.add_line((x_start, cur_y + 54), (x_start + 990, cur_y + 54), dxfattribs={'layer': 'TABLE_LINES'})

            except Exception as e:
                print(f"Warning: Could not draw summary table: {e}")
                traceback.print_exc()

        # Save File
        if filename is None:
            filename = f"{project_id}_layout.dxf"
        output_path = os.path.join(data_dir, filename)
        doc.saveas(output_path)
        return filename

    except Exception as e:
        print("CRITICAL ERROR in geometry_to_dxf:")
        traceback.print_exc()
        raise e
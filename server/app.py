from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from utils.land_loader import dxf_to_geojson
import shutil
import os
import uuid
from constraints import PlanningConstraints
import json
from geometry import generate_buildable_area, generate_main_road
from subdivision import generate_parcels
from metrics import calculate_metrics
from utils.utm_to_lat_long import to_lat_long
from export_dxf import geometry_to_dxf
from layout_variations import LayoutVariationGenerator

app = FastAPI(
    title="Industrial Estate Layout API",
    version="0.1.0"
)

DATA_DIR = "data"
CONFIG_DIR = "config"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

@app.get("/")
def ping(): 
    return {
        "status": "Ok",
        "message": "Server is running",
    }

@app.post("/upload")
async def upload_land(file: UploadFile = File(...)):
    # Validate File Extension
    if not file.filename.lower().endswith('.dxf'):
        raise HTTPException(status_code=400, detail="Only DXF files are supported.")

    # Create a unique project workspace
    project_id = str(uuid.uuid4())[:8]
    file_path = os.path.join(DATA_DIR, f"{project_id}_{file.filename}")

    # Save the uploaded file
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

    # Process DXF to GeoJSON and extract metrics
    output_geojson_name = f"{project_id}_map.geojson"
    output_geojson_path = os.path.join(DATA_DIR, output_geojson_name)
    
    try:
        land_data = dxf_to_geojson(file_path, output_geojson_path)
    except ValueError as ve:
        # Handle logic errors (e.g., "No valid closed polylines found")
        raise HTTPException(status_code=422, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Processing Error: {str(e)}")

    return {
        "project_id": project_id,
        "success": land_data["geometry_valid"],
        "total_site_area_sqm": land_data["area_sqm"],
        "total_site_area_rai": land_data["area_rai"],
        "entry_points_found": land_data["entry_point_count"],
        "obstacles_found": land_data["obstacle_count"],
        "existing_road_segments": land_data.get("road_segment_count", 0),
        "map_data_url": f"/data/{output_geojson_name}"
    }

@app.post("/set-constraints/{project_id}")
async def set_constraints(project_id: str, constraints: PlanningConstraints):
    config_path = os.path.join(CONFIG_DIR, f"{project_id}_constraints.json")
    
    try:
        with open(config_path, "w") as f: 
            json.dump(constraints.model_dump(), f, indent=4)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save constraints: {str(e)}")

    return {
        "project_id": project_id,
        "message": "Planning parameters saved successfully.",
    }

@app.get("/projects/{project_id}/generate")
async def get_layout_preview(project_id: str):
    try:
        buildable = generate_buildable_area(project_id, DATA_DIR, CONFIG_DIR)
        road = generate_main_road(project_id, DATA_DIR, CONFIG_DIR, buildable["raw_geom"])
        parcels = generate_parcels(
            project_id, 
            DATA_DIR, 
            CONFIG_DIR, 
            buildable["raw_geom"], 
            road["raw_geom"],
        )
        final_metrics = calculate_metrics(buildable, road, parcels)

        return {
            "type": "FeatureCollection",
            "features": [
                to_lat_long(buildable["feature"]), 
                to_lat_long(road["feature"])
            ] + to_lat_long(parcels),
            "properties": {
                "project_id": project_id,
                "metrics": final_metrics
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/projects/{project_id}/export/dxf")
async def export_dxf_layout(project_id: str):
    try:
        # Re-run logic to get raw objects
        buildable = generate_buildable_area(project_id, DATA_DIR, CONFIG_DIR)
        road = generate_main_road(project_id, DATA_DIR, CONFIG_DIR, buildable["raw_geom"])
        parcels_raw = generate_parcels(
            project_id, DATA_DIR, CONFIG_DIR, buildable["raw_geom"], road["raw_geom"]
        )
        metrics_input = [{"properties": p["properties"]} for p in parcels_raw]
        final_metrics = calculate_metrics(buildable, road, metrics_input)
        # Now we pass RAW objects directly to DXF writer
        # We don't need _raw_geom hacks anymore because 'parcels_raw' IS raw geometry
        dxf_filename = geometry_to_dxf(
            project_id, DATA_DIR, buildable, road, parcels_raw, metrics=final_metrics
        )
        
        return FileResponse(os.path.join(DATA_DIR, dxf_filename), filename=dxf_filename)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/projects/{project_id}/variations/export")
async def export_layout_variations(project_id: str):
    try:
        generator = LayoutVariationGenerator(project_id, DATA_DIR, CONFIG_DIR)
        exported_variations = generator.export_all_variations()
        
        return {
            "project_id": project_id,
            "status": "all_variations_generated",
            "variations": exported_variations,
            "files_generated": len([v for v in exported_variations if v.get("status") == "exported"]),
            "message": "All layout variations have been generated and exported to individual DXF files"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/projects/{project_id}/variations/preview")
async def preview_layout_variations(project_id: str):
    try:
        generator = LayoutVariationGenerator(project_id, DATA_DIR, CONFIG_DIR)
        variations = generator.generate_all_variations()
        
        preview_data = []
        for var in variations:
            if var["status"] == "success":
                preview_data.append({
                    "name": var["name"],
                    "description": var["description"],
                    "optimization_type": var["optimization_type"],
                    "parcel_mix": var["parcel_mix"],
                    "config": var["config"],
                    "kpi": var["kpi"],
                    "metrics": var["metrics"],
                    "net_buildable":var["net_buildable"]
                })
            else:
                preview_data.append({
                    "name": var["name"],
                    "description": var["description"],
                    "status": "error",
                    "error": var.get("error", "Unknown error")
                })
        
        return {
            "project_id": project_id,
            "variations": preview_data,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
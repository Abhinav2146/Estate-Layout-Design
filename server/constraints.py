from pydantic import BaseModel
from typing import List, Optional

class ParcelSizeTarget(BaseModel):
    size_group: str
    min_area: float
    max_area: float
    target_percent: float

class PlanningConstraints(BaseModel):
    project_id: str
    min_green_ratio: float = 0.1
    green_belt_setback_m: float = 10.0  
    retention_pond_target_percent: float = 0.07 
    wtp_target_percent: float = 0.02
    pond_setback_m: float = 10.0,
    industry_type: str = "Light Industry" 
    
    setback_boundary_m: float = 5.0
    buffer_obstacle_m: float = 3.0
    main_road_width_m: float = 12.0
    local_road_width_m: float = 8.0
    
    parcel_program: List[ParcelSizeTarget]
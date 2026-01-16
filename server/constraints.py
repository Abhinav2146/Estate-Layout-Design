from pydantic import BaseModel
from typing import List

class ParcelSizeTarget(BaseModel):
    size_group: str
    min_area: float
    max_area: float
    target_percent: float

class PlanningConstraints(BaseModel):
    project_id: str
    min_green_ratio: float = 0.10
    setback_boundary_m: float = 5.0
    buffer_obstacle_m: float = 3.0
    main_road_width_m: float = 12.0
    local_road_width_m: float = 8.0
    parcel_program: List[ParcelSizeTarget]
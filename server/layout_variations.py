from geometry import generate_buildable_area, generate_main_road
from subdivision import generate_parcels
from metrics import calculate_metrics, calculate_net_buildable_by_size
from export_dxf import geometry_to_dxf


class LayoutVariationGenerator:
    
    def __init__(self, project_id, data_dir, config_dir):
        self.project_id = project_id
        self.data_dir = data_dir
        self.config_dir = config_dir
        self.variations = []
        
    def generate_all_variations(self):
        variations = [
            self._generate_high_density_layout(),
            self._generate_balanced_layout(),
            self._generate_premium_layout()
        ]
        
        self.variations = variations
        return variations
    
    def _generate_high_density_layout(self):
        return self._generate_variation(
            name="High_Density",
            description="Maximum plots • Tight spacing • 40-80 sq.m parcels • Best for high-volume sales",
            optimization_type="density",
            road_spacing_config={
                "main_road_width": 15.0,      # Optimal for tight density
                "local_road_width": 8.5,      # Minimal local roads
                "vertical_spacing": 160,      # Very tight vertical
                "horizontal_spacing": 120     # Very tight horizontal
            },
            parcel_mix={
                "micro": 0.50,    # 40-60 sqm - 50% of plots
                "small": 0.35,    # 60-100 sqm - 35% of plots
                "medium": 0.15    # 100-150 sqm - 15% of plots
            },
            expected_metrics={
                "plot_count": "very_high",
                "road_ratio": "minimal",
                "green_ratio": "moderate",
                "avg_plot_size": "small"
            }
        )
    
    def _generate_balanced_layout(self):
        return self._generate_variation(
            name="Balanced",
            description="Optimal mix • Standard spacing • 80-150 sq.m parcels • Best for balanced returns",
            optimization_type="balanced",
            road_spacing_config={
                "main_road_width": 18.0,      # Standard main roads
                "local_road_width": 10.0,     # Standard local roads
                "vertical_spacing": 220,      # Medium vertical
                "horizontal_spacing": 160     # Medium horizontal
            },
            parcel_mix={
                "small": 0.35,     # 60-100 sqm - 35% of plots
                "medium": 0.45,    # 100-150 sqm - 45% of plots
                "large": 0.20      # 150-250 sqm - 20% of plots
            },
            expected_metrics={
                "plot_count": "medium",
                "road_ratio": "balanced",
                "green_ratio": "good",
                "avg_plot_size": "medium"
            }
        )
    
    def _generate_premium_layout(self):
        return self._generate_variation(
            name="Premium",
            description="Prestige parcels • Wide roads • 120-250 sq.m parcels • Best for premium positioning",
            optimization_type="premium",
            road_spacing_config={
                "main_road_width": 20.0,      # Wide main roads (tree-lined potential)
                "local_road_width": 13.0,     # Wide local roads
                "vertical_spacing": 290,      # Generous vertical
                "horizontal_spacing": 220     # Generous horizontal
            },
            parcel_mix={
                "medium": 0.35,    # 100-150 sqm - 35% of plots
                "large": 0.50,     # 150-250 sqm - 50% of plots
                "xlarge": 0.15     # 250+ sqm - 15% of plots (premium flagship lots)
            },
            expected_metrics={
                "plot_count": "low",
                "road_ratio": "generous",
                "green_ratio": "premium",
                "avg_plot_size": "large"
            }
        )
    
    def _generate_variation(self, name, description, optimization_type, road_spacing_config, parcel_mix, expected_metrics):
        try:
            # Load base geometry
            buildable = generate_buildable_area(self.project_id, self.data_dir, self.config_dir)
            road = generate_main_road(self.project_id, self.data_dir, self.config_dir, buildable["raw_geom"])
            
            # Generate parcels with SPECIFIC ROAD CONFIG
            parcels = generate_parcels(
                self.project_id,
                self.data_dir,
                self.config_dir,
                buildable["raw_geom"],
                road["raw_geom"],
                road_config=road_spacing_config  # <-- Pass the specific config
            )
            net_buildable = calculate_net_buildable_by_size(parcels)
            
            # Calculate metrics
            metrics = calculate_metrics(buildable, road, parcels)
            
            # Compute industry-standard KPIs
            total_saleable = metrics.get("land_use_budget", {}).get("saleable_area", {}).get("sqm", 1)
            total_plots = metrics.get("parcel_inventory", {}).get("total_plots", 1)
            total_roads = metrics.get("land_use_budget", {}).get("road_area", {}).get("sqm", 0)
            total_green = metrics.get("land_use_budget", {}).get("green_area", {}).get("sqm", 0)
            gross_site = metrics.get("site_analysis", {}).get("total_site_sqm", 1)
            
            avg_plot_size = total_saleable / total_plots if total_plots > 0 else 0
            
            # Calculate industry KPIs
            road_efficiency = (1 - (total_roads / gross_site)) * 100 if gross_site > 0 else 0
            green_ratio = (total_green / gross_site) * 100 if gross_site > 0 else 0
            
            return {
                "name": name,
                "description": description,
                "optimization_type": optimization_type,
                "parcel_mix": parcel_mix,
                "config": road_spacing_config,
                "buildable_geom": buildable,
                "road_geom": road,
                "parcels": parcels,
                "metrics": metrics,
                "net_buildable": net_buildable,
                "kpi": {
                    "total_plots": total_plots,
                    "avg_plot_size_sqm": round(avg_plot_size, 2),
                    "road_efficiency_percent": round(road_efficiency, 1),
                    "green_coverage_percent": round(green_ratio, 1),
                    "total_saleable_sqm": round(total_saleable, 0),
                },
                "status": "success"
            }
            
        except Exception as e:
            return {
                "name": name,
                "description": description,
                "optimization_type": optimization_type,
                "config": road_spacing_config,
                "status": "error",
                "error": str(e)
            }
    
    def export_all_variations(self):
        if not self.variations:
            self.generate_all_variations()
        
        exported_files = []
        
        for variation in self.variations:
            if variation["status"] == "success":
                try:
                    filename = self._export_variation_to_dxf(variation)
                    exported_files.append({
                        "name": variation["name"],
                        "filename": filename,
                        "description": variation["description"],
                        "optimization_type": variation["optimization_type"],
                        "kpi": variation["kpi"],
                        "metrics": variation["metrics"],
                        "status": "exported"
                    })
                except Exception as e:
                    exported_files.append({
                        "name": variation["name"],
                        "status": "export_failed",
                        "error": str(e)
                    })
            else:
                exported_files.append({
                    "name": variation["name"],
                    "status": "generation_failed",
                    "error": variation.get("error", "Unknown error")
                })
        
        return exported_files
    
    def _export_variation_to_dxf(self, variation):
        name = variation["name"]
        buildable = variation["buildable_geom"]
        road = variation["road_geom"]
        parcels = variation["parcels"]
        metrics = variation["metrics"]
        
        # Create filename with variation name
        filename = f"{self.project_id}_layout_{name}.dxf"
        
        # Export to DXF
        geometry_to_dxf(
            self.project_id,
            self.data_dir,
            buildable,
            road,
            parcels,
            metrics=metrics,
            filename=filename
        )
        
        return filename

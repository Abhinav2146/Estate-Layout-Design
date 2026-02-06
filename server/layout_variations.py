from geometry import generate_buildable_area, generate_main_road
from subdivision import generate_parcels
from metrics import calculate_metrics, calculate_net_buildable_by_size
from export_dxf import geometry_to_dxf
import math


class LayoutVariationGenerator:

    def __init__(self, project_id, data_dir, config_dir):
        self.project_id = project_id
        self.data_dir = data_dir
        self.config_dir = config_dir
        self.variations = []

    # ---------------------------------------------------------
    # MAIN ENTRY
    # ---------------------------------------------------------
    def generate_all_variations(self):
        """
        Generate 10 layouts, score them,
        select TOP 3 with visual diversity
        """

        buildable = generate_buildable_area(
            self.project_id, self.data_dir, self.config_dir
        )
        road = generate_main_road(
            self.project_id, self.data_dir, self.config_dir, buildable["raw_geom"]
        )
        entry_points = buildable.get("entry_points", [])

        # ---- 10 candidate spacing configs ----
        candidate_configs = [
            {"vertical_spacing": 300, "horizontal_spacing": 220},
            {"vertical_spacing": 320, "horizontal_spacing": 240},
            {"vertical_spacing": 340, "horizontal_spacing": 260},
            {"vertical_spacing": 360, "horizontal_spacing": 280},
            {"vertical_spacing": 380, "horizontal_spacing": 300},
            {"vertical_spacing": 400, "horizontal_spacing": 320},
            {"vertical_spacing": 420, "horizontal_spacing": 340},
            {"vertical_spacing": 440, "horizontal_spacing": 360},
            {"vertical_spacing": 460, "horizontal_spacing": 380},
            {"vertical_spacing": 480, "horizontal_spacing": 400},
        ]

        evaluated = []

        for i, cfg in enumerate(candidate_configs):
            result = self._generate_variation(
                name=f"Layout_{i}",
                road_spacing_config=cfg,
                buildable=buildable,
                road=road,
                entry_points=entry_points
            )

            if result["status"] == "success":
                result["score"] = self._score_layout(result)
                result["signature"] = self._layout_signature(result)
                evaluated.append(result)

        # ---- Sort by score ----
        evaluated.sort(key=lambda x: x["score"], reverse=True)

        # ---- Pick top 3 with diversity ----
        selected = []
        for candidate in evaluated:
            if len(selected) == 0:
                selected.append(candidate)
                continue

            if all(
                self._signature_distance(candidate["signature"], s["signature"]) > 0.25
                for s in selected
            ):
                selected.append(candidate)

            if len(selected) == 3:
                break

        self.variations = selected
        return selected

    # ---------------------------------------------------------
    # SINGLE VARIATION
    # ---------------------------------------------------------
    def _generate_variation(self, name, road_spacing_config, buildable, road, entry_points):
        try:
            parcels = generate_parcels(
                self.project_id,
                self.data_dir,
                self.config_dir,
                buildable["raw_geom"],
                road["raw_geom"],
                entry_points,
                road_config=road_spacing_config
            )

            metrics = calculate_metrics(buildable, road, parcels)
            net_buildable = calculate_net_buildable_by_size(parcels)

            total_saleable = metrics["land_use_budget"]["saleable_area"]["sqm"]
            total_plots = metrics["parcel_inventory"]["total_plots"]
            total_roads = metrics["land_use_budget"]["road_area"]["sqm"]
            total_green = metrics["land_use_budget"]["green_area"]["sqm"]
            gross_site = metrics["site_analysis"]["total_site_sqm"]

            avg_plot_size = total_saleable / total_plots if total_plots else 0

            return {
                "name": name,
                "road_config": road_spacing_config,
                "buildable_geom": buildable,
                "road_geom": road,
                "parcels": parcels,
                "metrics": metrics,
                "net_buildable": net_buildable,
                "kpi": {
                    "total_plots": total_plots,
                    "avg_plot_size_sqm": round(avg_plot_size, 0),
                    "road_efficiency_percent": round((1 - total_roads / gross_site) * 100, 1),
                    "green_coverage_percent": round((total_green / gross_site) * 100, 1),
                    "land_utilization_percent": round((total_saleable / gross_site) * 100, 1),
                    "total_saleable_sqm": round(total_saleable, 0),
                },
                "status": "success"
            }

        except Exception as e:
            return {
                "name": name,
                "status": "error",
                "error": str(e)
            }

    # ---------------------------------------------------------
    # SCORING
    # ---------------------------------------------------------
    def _score_layout(self, variation):
        k = variation["kpi"]

        green_penalty = max(0, 10 - k["green_coverage_percent"]) * 5

        return round(
            3.0 * k["land_utilization_percent"] +
            2.0 * k["road_efficiency_percent"] -
            green_penalty,
            2
        )

    # ---------------------------------------------------------
    # VISUAL DIVERSITY
    # ---------------------------------------------------------
    def _layout_signature(self, variation):
        k = variation["kpi"]
        cfg = variation["road_config"]

        return [
            k["total_plots"] / 100,
            k["avg_plot_size_sqm"] / 5000,
            k["road_efficiency_percent"] / 100,
            cfg["vertical_spacing"] / cfg["horizontal_spacing"],
        ]

    def _signature_distance(self, a, b):
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

    # ---------------------------------------------------------
    # EXPORT
    # ---------------------------------------------------------
    def export_all_variations(self):
        if not self.variations:
            self.generate_all_variations()

        exported = []

        for v in self.variations:
            filename = f"{self.project_id}_layout_{v['name']}.dxf"

            geometry_to_dxf(
                self.project_id,
                self.data_dir,
                v["buildable_geom"],
                v["road_geom"],
                v["parcels"],
                metrics=v["metrics"],
                filename=filename
            )

            exported.append({
                "name": v["name"],
                "filename": filename,
                "score": v["score"],
                "kpi": v["kpi"],
                "status": "exported"
            })

        return exported

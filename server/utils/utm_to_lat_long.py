import pyproj
from shapely.ops import transform
from shapely.geometry import mapping

transformer = pyproj.Transformer.from_crs("EPSG:32647", "EPSG:4326", always_xy=True).transform

OFFSET_X = -280
OFFSET_Y = 350
def calibrate_geom(geom):
    def shift(x, y):
        return (x + OFFSET_X, y + OFFSET_Y)
    return transform(shift, geom)

def to_lat_long(obj):
    # Handle GeoJSON feature dictionaries
    if isinstance(obj, dict):
        if "geometry" in obj:
            # It's a GeoJSON feature
            feature = obj.copy()
            feature["geometry"] = to_lat_long(feature["geometry"])
            return feature
        elif "type" in obj and obj["type"] in ["Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"]:
            # It's a GeoJSON geometry object
            from shapely.geometry import shape
            geom = shape(obj)
            if geom.is_empty:
                return None
            calibrated_geom = calibrate_geom(geom)
            latlong_geom = transform(transformer, calibrated_geom)
            return mapping(latlong_geom)
        return obj
    
    # Handle Shapely geometries
    if hasattr(obj, 'is_empty'):
        if obj.is_empty:
            return None
        
        calibrated_geom = calibrate_geom(obj)
        latlong_geom = transform(transformer, calibrated_geom)
        return mapping(latlong_geom)
    
    # Handle lists (for parcel features)
    if isinstance(obj, list):
        return [to_lat_long(item) for item in obj]
    
    return obj
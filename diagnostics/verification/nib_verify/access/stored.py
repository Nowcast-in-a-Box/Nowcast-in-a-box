"""Run-bound real fields for verification; arrays are loaded once per cube."""

from .base import (
    Capabilities,
    Field,
    FieldUnavailable,
    PredictionUnavailable,
    ProjectedCoords,
    Coords,
)
from utils.stored_fields import StoredFields, FieldUnavailable as StoredUnavailable


class RunAccess:
    def __init__(self, directory):
        self.fields = StoredFields(directory)

    def capabilities(self):
        caps = self.fields.capabilities()
        return Capabilities(
            cadence_minutes=caps["cadence_minutes"],
            observation_names=tuple(caps["observation_names"]),
            model_ids=tuple(caps["model_ids"]),
            units={
                name: caps["unit"]
                for name in caps["observation_names"] + caps["model_ids"]
            },
        )

    def _get(self, operation, name, *args):
        try:
            value = getattr(self.fields, operation)(name, *args)
        except StoredUnavailable as exc:
            error = (
                PredictionUnavailable
                if operation in ("get_prediction", "list_predictions")
                else FieldUnavailable
            )
            raise error(str(exc)) from exc
        if not isinstance(value, dict):
            return value
        crs = value["grid"]["crs"]
        coords = (
            Coords(value["x"], value["y"])
            if crs == "EPSG:4326"
            else ProjectedCoords(value["x"], value["y"], crs)
        )
        return Field(
            data=value["data"],
            coords=coords,
            valid_time=value["valid_time"],
            unit=value["unit"],
            variable=value["variable"],
            name=name,
            mask=value["mask"],
        )

    def list_observations(self, name, t0, t1):
        return self._get("list_observations", name, t0, t1)

    def get_observation(self, name, valid_time):
        return self._get("get_observation", name, valid_time)

    def list_predictions(self, model_id, init_time):
        return self._get("list_predictions", model_id, init_time)

    def get_prediction(self, model_id, init_time, lead_minutes):
        return self._get("get_prediction", model_id, init_time, lead_minutes)

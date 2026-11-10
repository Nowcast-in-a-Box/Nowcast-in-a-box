"""MLCast catalog source: radklim_5_minutes. Local sample only."""
from interfaces.data import ChannelSpec, DataCapabilities, SpatialSpec, VariableSpec
from adapters.mlcast_common import MLCastZarrSource

class Source(MLCastZarrSource):
    CAPABILITIES = DataCapabilities(
        id='mlcast_radklim_5_minutes', version="1.0.0", title='Germany DWD RADKLIM 5 min',
        instrument="radar composite", channels=ChannelSpec(
            mode="closed", variables=(VariableSpec('rainfall_amount', 'radar-derived_5min_precipitation_amount', 'mm'),),
        ), spatial=SpatialSpec(crop=True),
        native_cadence_minutes=5, formats=("zarr",),
    )

ADAPTER = Source()

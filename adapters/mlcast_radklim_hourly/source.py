"""MLCast catalog source: radklim_hourly. Local sample only."""
from interfaces.data import ChannelSpec, DataCapabilities, SpatialSpec, VariableSpec
from adapters.mlcast_common import MLCastZarrSource

class Source(MLCastZarrSource):
    CAPABILITIES = DataCapabilities(
        id='mlcast_radklim_hourly', version="1.0.0", title='Germany DWD RADKLIM hourly',
        instrument="radar composite", channels=ChannelSpec(
            mode="closed", variables=(VariableSpec('rainfall_amount', 'radar-derived_hourly_precipitation_amount', 'mm'),),
        ), spatial=SpatialSpec(crop=True),
        native_cadence_minutes=60, formats=("zarr",),
    )

ADAPTER = Source()

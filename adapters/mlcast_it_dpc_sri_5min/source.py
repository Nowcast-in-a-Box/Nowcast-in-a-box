"""MLCast catalog source: it_dpc_sri_5min. Local sample only."""
from interfaces.data import ChannelSpec, DataCapabilities, SpatialSpec, VariableSpec
from adapters.mlcast_common import MLCastZarrSource

class Source(MLCastZarrSource):
    CAPABILITIES = DataCapabilities(
        id='mlcast_it_dpc_sri_5min', version="1.0.0", title='Italy DPC surface rainfall intensity',
        instrument="radar composite", channels=ChannelSpec(
            mode="closed", variables=(VariableSpec('RR', 'surface_rainfall_intensity', 'mm/h'),),
        ), spatial=SpatialSpec(crop=True),
        native_cadence_minutes=5, formats=("zarr",),
    )

ADAPTER = Source()

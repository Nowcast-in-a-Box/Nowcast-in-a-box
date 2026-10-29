"""MLCast catalog source: uk_metoffice_5min. Local sample only."""
from interfaces.data import ChannelSpec, DataCapabilities, SpatialSpec, VariableSpec
from adapters.mlcast_common import MLCastZarrSource

class Source(MLCastZarrSource):
    CAPABILITIES = DataCapabilities(
        id='mlcast_uk_metoffice_5min', version="1.0.0", title='UK Met Office radar precipitation',
        instrument="radar composite", channels=ChannelSpec(
            mode="closed", variables=(VariableSpec('RR', 'rainfall_rate', 'mm/h'),),
        ), spatial=SpatialSpec(crop=True),
        native_cadence_minutes=5, formats=("zarr",),
    )

ADAPTER = Source()

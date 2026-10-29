"""MLCast catalog source: dmi_10_minutes. Local sample only."""
from interfaces.data import ChannelSpec, DataCapabilities, SpatialSpec, VariableSpec
from adapters.mlcast_common import MLCastZarrSource

class Source(MLCastZarrSource):
    CAPABILITIES = DataCapabilities(
        id='mlcast_dmi_10_minutes', version="1.0.0", title='Denmark DMI radar reflectivity',
        instrument="radar composite", channels=ChannelSpec(
            mode="closed", variables=(VariableSpec('dbz', 'radar_reflectivity', 'dBZ'),),
        ), spatial=SpatialSpec(crop=True),
        native_cadence_minutes=10, formats=("zarr",),
    )

ADAPTER = Source()

"""MLCast catalog source: be_rmi_radclim_mfb_5min. Local sample only."""
from interfaces.data import ChannelSpec, DataCapabilities, SpatialSpec, VariableSpec
from adapters.mlcast_common import MLCastZarrSource

class Source(MLCastZarrSource):
    CAPABILITIES = DataCapabilities(
        id='mlcast_be_rmi_radclim_mfb_5min', version="1.0.0", title='Belgium RMI RADCLIM MFB',
        instrument="radar composite", channels=ChannelSpec(
            mode="closed", variables=(VariableSpec('rain_rate', 'bias_corrected_rainfall_rate', 'mm/h'),),
        ), spatial=SpatialSpec(crop=True),
        native_cadence_minutes=5, formats=("zarr",),
    )

ADAPTER = Source()

"""Local OPERA+DPC VMI reader. Network retrieval belongs to orchestration."""

import numpy as np

from interfaces.data import ChannelSpec, DataCapabilities, SpatialSpec, VariableSpec
from utils.mlcast_zarr import MLCastZarrSource


class Source(MLCastZarrSource):
    CAPABILITIES = DataCapabilities(
        id="mlcast_opera_plus_it_vmi",
        version="1.0.0",
        title="OPERA + Italy DPC VMI",
        instrument="radar composite",
        channels=ChannelSpec(
            mode="closed",
            variables=(VariableSpec("vmi", "vertical_maximum_intensity", "dBZ"),),
        ),
        spatial=SpatialSpec(crop=True),
        native_cadence_minutes=5,
        formats=("zarr",),
    )

    def load(self, request):
        bundle = super().load(request)
        for fields in bundle["data"].values():
            frame = fields["vmi"]
            # -9998 is documented covered/no echo, rather than missing coverage.
            frame.coords["no_echo_mask"] = (frame.dims, frame.values == -9998)
            frame.coords["valid_mask"] = (frame.dims, np.isfinite(frame.values))
        bundle["metadata"].update(
            no_echo_value=-9998,
            special_values_policy=(
                "Native values retained; -9998 is covered no echo and CF missing "
                "values decode to NaN. The model bridge maps only covered no echo "
                "to its configured dBZ floor."
            ),
        )
        return bundle


ADAPTER = Source()

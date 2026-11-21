"""Copy into adapters/<data-id>/<model-id>/ and implement your backend."""

from pathlib import Path
from typing import Any

from interfaces.fields import Forecast, Observations
from interfaces.model import DeclaredModelAdapter


class Adapter(DeclaredModelAdapter):
    declaration_path = (
        Path(__file__).resolve().parents[3]
        / "atlas/contracts/models/example_data/example_model.json"
    )

    def prepare(self, observations: Observations) -> Any:
        self.validate_input(observations)
        raise NotImplementedError("Implement backend input preparation")

    def predict(self, prepared: Any, *, weights_path: str) -> Forecast:
        raise NotImplementedError(
            "Verify local weights and implement offline inference"
        )


ADAPTER = Adapter()

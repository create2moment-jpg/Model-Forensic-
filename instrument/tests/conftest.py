import pytest

# The toy model needs torch; tests that only need numpy (attention stats, naming, sinks) run without it.
try:
    import torch
except ImportError:  # pragma: no cover - depends on the environment
    torch = None  # type: ignore[assignment]

if torch is None:

    @pytest.fixture()
    def model():  # type: ignore[no-untyped-def]
        pytest.skip("torch is not installed")

    @pytest.fixture()
    def batch():  # type: ignore[no-untyped-def]
        pytest.skip("torch is not installed")

else:
    from tests._toy import ToyFusionDetector

    @pytest.fixture()
    def model() -> "ToyFusionDetector":
        torch.manual_seed(0)
        return ToyFusionDetector()

    @pytest.fixture()
    def batch():  # type: ignore[no-untyped-def]
        torch.manual_seed(1)
        return torch.randn(4, 3, 16, 16), torch.randn(4, 2, 16, 16), torch.randint(0, 3, (4,))

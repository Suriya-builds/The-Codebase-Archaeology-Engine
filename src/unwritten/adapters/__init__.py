from unwritten.adapters.python import PythonAdapter
from unwritten.errors import HarnessError

ADAPTERS = (PythonAdapter(),)


def detect_adapter(root):
    """Return the adapter for the repository at ``root`` or raise HarnessError."""
    for adapter in ADAPTERS:
        if adapter.detect(root):
            return adapter
    supported = ", ".join(a.name for a in ADAPTERS)
    raise HarnessError(
        f"no language adapter recognises this repository (supported: {supported})")

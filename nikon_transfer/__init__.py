"""nikon_transfer — Wi-Fi photo transfer from Nikon D5300 via PTP/IP."""

# Source unique de la version du package — lue par pyproject.toml (via
# `tool.setuptools.dynamic`), par la CLI (`--version`), par la GUI (titre),
# et par le .spec PyInstaller (Info.plist). Modifier ici, jamais ailleurs.
__version__ = "0.0.1"
__all__ = ["PtpIpClient", "transfer_photos", "discover_camera", "__version__"]

from .client import PtpIpClient
from .transfer import discover_camera, transfer_photos

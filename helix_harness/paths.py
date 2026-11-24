"""Product code, private user data and frozen engine inputs have separate roots."""

from pathlib import Path

import mewcode

ENGINE_ROOT = Path(mewcode.__file__).resolve().parents[1]
PROJECT = Path(__file__).resolve().parents[1]
APP_DATA = Path.home() / "Library/Application Support/Helix Harness"
PROVIDER_CONFIG = APP_DATA / "providers.yaml"
# Keep historical credentials and archives protected when browsing a parent directory.
LEGACY_DATA = Path.home() / "Library/Application Support/MewCode"

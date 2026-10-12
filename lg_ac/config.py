"""Load and save the app's settings (access token, country, chosen device)."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "lg-ac-desktop" / "config.json"


@dataclass
class Config:
    access_token: str = ""
    country_code: str = "BR"
    # LG asks every client to send a stable, random ID; generate it once and keep it.
    client_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    device_id: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(self.access_token and self.country_code)


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return Config()
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
    return Config(**known)


def save_config(config: Config, path: Path | None = None) -> None:
    """Write the config readable only by the current user, since it holds the token."""
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(asdict(config), f, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(path)

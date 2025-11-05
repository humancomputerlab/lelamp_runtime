#!/usr/bin/env python
"""Utility functions for managing device configuration (ID and port)."""

import json
import os
from pathlib import Path

# Store config file in the service directory
SERVICE_DIR = Path(__file__).parent
CONFIG_FILE = SERVICE_DIR / ".lelamp_config.json"


def save_config(lamp_id: str, port: str) -> None:
    """Save lamp ID and port to config file."""
    config = {
        "id": lamp_id,
        "port": port
    }
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
    print(f"Config saved to {CONFIG_FILE} with ID {lamp_id} and port {port}")


def load_config() -> tuple[str | None, str | None]:
    """Load lamp ID and port from config file. Returns (id, port) or (None, None) if not found."""
    if not CONFIG_FILE.exists():
        return None, None
    
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        return config.get("id"), config.get("port")
    except (json.JSONDecodeError, KeyError, IOError):
        return None, None


def get_config_path() -> str:
    """Return the path to the config file."""
    return str(CONFIG_FILE)


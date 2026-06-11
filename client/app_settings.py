import json
from pathlib import Path


SETTINGS_FILE = Path(__file__).resolve().parent.parent / "user_settings.json"
DEFAULT_SETTINGS = {
    "default_dark_mode": False,
    "input_mode": "clipboard",
    "replace_mode": False,
    "history_enabled": False,
}


def load_app_settings():
    if not SETTINGS_FILE.exists():
        return DEFAULT_SETTINGS.copy()

    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return DEFAULT_SETTINGS.copy()

    settings = DEFAULT_SETTINGS.copy()
    if isinstance(data, dict):
        settings.update({key: data[key] for key in DEFAULT_SETTINGS if key in data})
    return settings


def save_app_settings(settings):
    data = DEFAULT_SETTINGS.copy()
    if isinstance(settings, dict):
        data.update({key: settings[key] for key in DEFAULT_SETTINGS if key in settings})
    SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

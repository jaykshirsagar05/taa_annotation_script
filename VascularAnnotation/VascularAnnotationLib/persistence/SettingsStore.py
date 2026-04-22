"""
SettingsStore.py - Application-level settings persistence via Qt QSettings.

Note: qt is imported INSIDE methods to allow unit testing without Slicer.
"""


class SettingsStore:
    """
    Thin wrapper around qt.QSettings for VascularAnnotation plugin settings.

    All keys have sensible defaults so the plugin works out-of-the-box.
    """

    APP_NAME = "VascularAnnotation"
    ORG_NAME = "VascularAnnotation"

    DEFAULTS = {
        "orthanc_url": "http://localhost:8042",
        "admin_dashboard_url": "http://localhost:8000",
        "ct_window_width": 1200,
        "ct_window_level": 350,
        "backend_type": "local",
        "auto_update_enabled": False,
        "auto_update_repo": "",
        "local_watched_folder": "",
    }

    def _settings(self):
        """Return a QSettings instance (imported lazily to allow unit testing)."""
        import qt  # noqa: PLC0415 – intentional lazy import
        return qt.QSettings(self.ORG_NAME, self.APP_NAME)

    # ------------------------------------------------------------------
    # Generic get / set
    # ------------------------------------------------------------------

    def get(self, key: str, default=None):
        """Get a setting value, falling back to DEFAULTS then to *default*."""
        fallback = self.DEFAULTS.get(key, default)
        try:
            s = self._settings()
            value = s.value(key, fallback)
            # QSettings may return string for booleans; coerce known bool keys.
            if key in ("auto_update_enabled",):
                if isinstance(value, str):
                    return value.lower() in ("true", "1", "yes")
                return bool(value)
            # Coerce known int keys
            if key in ("ct_window_width", "ct_window_level"):
                return int(value)
            return value
        except Exception:
            return fallback

    def set(self, key: str, value) -> None:
        """Persist a setting value."""
        try:
            s = self._settings()
            s.setValue(key, value)
            s.sync()
        except Exception as e:
            print(f"[SettingsStore] WARNING: Could not save setting '{key}': {e}")

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def orthanc_url(self) -> str:
        return self.get("orthanc_url")

    @orthanc_url.setter
    def orthanc_url(self, v: str):
        self.set("orthanc_url", v)

    @property
    def admin_dashboard_url(self) -> str:
        return self.get("admin_dashboard_url")

    @admin_dashboard_url.setter
    def admin_dashboard_url(self, v: str):
        self.set("admin_dashboard_url", v)

    @property
    def ct_window_width(self) -> int:
        return self.get("ct_window_width")

    @ct_window_width.setter
    def ct_window_width(self, v: int):
        self.set("ct_window_width", int(v))

    @property
    def ct_window_level(self) -> int:
        return self.get("ct_window_level")

    @ct_window_level.setter
    def ct_window_level(self, v: int):
        self.set("ct_window_level", int(v))

    @property
    def backend_type(self) -> str:
        return self.get("backend_type")

    @backend_type.setter
    def backend_type(self, v: str):
        self.set("backend_type", v)

    @property
    def local_watched_folder(self) -> str:
        return self.get("local_watched_folder")

    @local_watched_folder.setter
    def local_watched_folder(self, v: str):
        self.set("local_watched_folder", v)

    @property
    def auto_update_enabled(self) -> bool:
        return self.get("auto_update_enabled")

    @auto_update_enabled.setter
    def auto_update_enabled(self, v: bool):
        self.set("auto_update_enabled", bool(v))

    @property
    def auto_update_repo(self) -> str:
        return self.get("auto_update_repo")

    @auto_update_repo.setter
    def auto_update_repo(self, v: str):
        self.set("auto_update_repo", v)

    # ------------------------------------------------------------------
    # Bulk helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a snapshot of all known settings as a dict."""
        return {k: self.get(k) for k in self.DEFAULTS}

    def reset_to_defaults(self) -> None:
        """Reset all settings to their defaults."""
        for k, v in self.DEFAULTS.items():
            self.set(k, v)


# Module-level singleton
settings = SettingsStore()

"""
ProfileRegistry.py - Registry for discovering and managing VascularProfiles.

Profiles are loaded from:
  1. Built-in profiles: Resources/Profiles/ (shipped with the plugin)
  2. User profiles: ~/.vascular-annotation/profiles/ (user-added)

Auto-detection uses each profile's input file_patterns to match folder contents.
"""
import os
import glob as _glob
from typing import Optional, Tuple

from .ProfileSchema import VascularProfile
from .ProfileLoader import ProfileLoader


class ProfileRegistry:
    """Central registry for all available VascularProfiles."""

    BUILTIN_DIR = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "Resources", "Profiles")
    )
    USER_DIR = os.path.expanduser("~/.vascular-annotation/profiles")

    # In-process cache: {profile_id: VascularProfile}
    _cache: Optional[dict] = None

    @classmethod
    def discover(cls) -> dict:
        """
        Load all profiles from built-in and user directories.

        Returns:
            dict mapping profile_id -> VascularProfile
        """
        if cls._cache is not None:
            return cls._cache

        profiles = {}
        for directory in (cls.BUILTIN_DIR, cls.USER_DIR):
            if not os.path.isdir(directory):
                continue
            for json_file in sorted(_glob.glob(os.path.join(directory, "*.json"))):
                try:
                    profile = ProfileLoader.load(json_file)
                    profiles[profile.id] = profile
                    print(f"[ProfileRegistry] Loaded profile '{profile.id}' from {json_file}")
                except Exception as e:
                    print(f"[ProfileRegistry] WARNING: Failed to load '{json_file}': {e}")

        cls._cache = profiles
        return profiles

    @classmethod
    def get(cls, profile_id: str) -> VascularProfile:
        """
        Get a profile by ID.

        Raises:
            KeyError: if no profile with the given ID is found.
        """
        profiles = cls.discover()
        if profile_id not in profiles:
            raise KeyError(
                f"No profile with id='{profile_id}'. "
                f"Available: {sorted(profiles.keys())}"
            )
        return profiles[profile_id]

    @classmethod
    def register(cls, profile: VascularProfile) -> None:
        """Add or replace a profile in the in-process registry."""
        if cls._cache is None:
            cls._cache = {}
        cls._cache[profile.id] = profile
        print(f"[ProfileRegistry] Registered profile '{profile.id}'")

    @classmethod
    def invalidate_cache(cls) -> None:
        """Force re-discovery on next call to discover()."""
        cls._cache = None

    @classmethod
    def list_ids(cls) -> list:
        """Return sorted list of all registered profile IDs."""
        return sorted(cls.discover().keys())

    @classmethod
    def detect_from_folder(cls, folder_path: str) -> Tuple[Optional[VascularProfile], dict]:
        """
        Auto-detect a matching profile by scanning a folder for files.

        Algorithm (per profile, in discovery order):
          - For each required input, try each file_pattern (with {id} replaced by '*')
          - A profile matches when ALL required inputs have at least one file match.
          - Returns the FIRST matching profile and a dict of found file paths.

        Returns:
            Tuple of (VascularProfile or None, dict {logical_name: file_path})
        """
        profiles = cls.discover()
        if not os.path.isdir(folder_path):
            return None, {}

        # Collect all files in the folder for fast matching
        all_files = os.listdir(folder_path)

        def _match_patterns(patterns: list, folder: str) -> Optional[str]:
            """Return first file matching any pattern, or None."""
            for pattern in patterns:
                # Replace {id} with wildcard for glob matching
                glob_pattern = pattern.replace("{id}", "*")
                matched = _glob.glob(os.path.join(folder, glob_pattern))
                if matched:
                    return matched[0]
            return None

        # Collect found files for the best profile
        for profile_id in cls.list_ids():
            profile = profiles[profile_id]
            found = {}
            all_required_found = True

            # Check required inputs
            for input_name, input_spec in profile.inputs.items():
                path = _match_patterns(input_spec.file_patterns, folder_path)
                if path:
                    found[input_name] = path
                elif input_spec.required:
                    all_required_found = False
                    break

            if all_required_found and found:
                # Also try to find optional inputs
                for input_name, input_spec in profile.inputs.items():
                    if not input_spec.required and input_name not in found:
                        path = _match_patterns(input_spec.file_patterns, folder_path)
                        if path:
                            found[input_name] = path
                print(
                    f"[ProfileRegistry] Auto-detected profile '{profile_id}' "
                    f"in folder '{folder_path}'"
                )
                return profile, found

        return None, {}

    @classmethod
    def detect_from_attachments(
        cls, available_attachments: dict
    ) -> Optional[VascularProfile]:
        """
        Detect a profile from a dict of available Orthanc attachment names.

        Args:
            available_attachments: dict mapping logical names to bool or attachment info.
                  E.g. {"ct": True, "seg_mask": True, "centerline": False}

        Returns:
            VascularProfile or None if no profile matches.
        """
        profiles = cls.discover()
        for profile_id in cls.list_ids():
            profile = profiles[profile_id]
            # Check all required inputs
            if all(
                available_attachments.get(name, False)
                for name, spec in profile.inputs.items()
                if spec.required
            ):
                return profile
        return None

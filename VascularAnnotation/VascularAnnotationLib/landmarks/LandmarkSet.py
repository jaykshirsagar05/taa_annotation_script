"""
LandmarkSet.py - Data classes for landmark definitions loaded from JSON.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class LandmarkDef:
    """Definition of a single landmark point."""
    id: int
    label: str
    name: str
    description: str
    color: list          # [R, G, B] floats 0-1
    zone_after: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "LandmarkDef":
        return cls(
            id=d["id"],
            label=d["label"],
            name=d["name"],
            description=d.get("description", ""),
            color=d.get("color", [1.0, 1.0, 1.0]),
            zone_after=d.get("zone_after"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "name": self.name,
            "description": self.description,
            "color": self.color,
            "zone_after": self.zone_after,
        }


@dataclass
class LandmarkSet:
    """
    A named, ordered collection of landmark definitions for a specific anatomy.
    """
    id: str
    name: str
    description: str
    max_landmarks: int
    landmarks: List[LandmarkDef]
    landmark_schema_version: str = "1.0"

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_json(cls, path: str) -> "LandmarkSet":
        """Load a LandmarkSet from a JSON file."""
        if not os.path.isfile(path):
            raise FileNotFoundError(f"LandmarkSet JSON not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        landmarks = [LandmarkDef.from_dict(lm) for lm in data.get("landmarks", [])]
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            max_landmarks=data.get("max_landmarks", len(landmarks)),
            landmarks=landmarks,
            landmark_schema_version=data.get("landmark_schema_version", "1.0"),
        )

    @classmethod
    def from_profile_id(cls, landmark_set_id: str) -> "LandmarkSet":
        """
        Load a LandmarkSet by its ID from the built-in Resources/LandmarkSets/ folder.

        Args:
            landmark_set_id: e.g. "aorta_svs_sts", "carotid_bifurcation", "generic_n"
        """
        builtin_dir = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__), "..", "..", "Resources", "LandmarkSets"
            )
        )
        json_path = os.path.join(builtin_dir, f"{landmark_set_id}.json")
        if os.path.isfile(json_path):
            return cls.from_json(json_path)

        # Fallback: check user directory
        user_dir = os.path.expanduser("~/.vascular-annotation/landmark-sets")
        user_path = os.path.join(user_dir, f"{landmark_set_id}.json")
        if os.path.isfile(user_path):
            return cls.from_json(user_path)

        # Last resort: return a generic set
        print(
            f"[LandmarkSet] WARNING: LandmarkSet '{landmark_set_id}' not found; "
            f"using generic_n fallback."
        )
        return cls.get_generic()

    @classmethod
    def get_generic(cls, n: int = 5) -> "LandmarkSet":
        """Create a generic N-point landmark set on the fly."""
        colors = [
            [1.0, 0.20, 0.20],
            [1.0, 0.55, 0.0],
            [0.90, 0.85, 0.0],
            [0.20, 0.80, 0.20],
            [0.20, 0.40, 1.0],
            [0.0, 0.80, 0.80],
            [0.55, 0.0, 1.0],
            [0.90, 0.0, 0.90],
            [0.80, 0.40, 0.15],
            [0.55, 0.55, 0.55],
        ]
        landmarks = []
        for i in range(n):
            color = colors[i % len(colors)]
            landmarks.append(
                LandmarkDef(
                    id=i,
                    label=f"P{i + 1}",
                    name=f"Point {i + 1}",
                    description=f"Generic landmark point {i + 1}.",
                    color=color,
                    zone_after=f"Segment {i + 1}" if i < n - 1 else None,
                )
            )
        return cls(
            id="generic_n",
            name=f"Generic {n}-Point",
            description=f"Auto-generated generic landmark set with {n} points.",
            max_landmarks=n,
            landmarks=landmarks,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_by_index(self, index: int) -> Optional[LandmarkDef]:
        if 0 <= index < len(self.landmarks):
            return self.landmarks[index]
        return None

    def get_by_label(self, label: str) -> Optional[LandmarkDef]:
        for lm in self.landmarks:
            if lm.label == label:
                return lm
        return None

    def to_dict(self) -> dict:
        return {
            "landmark_schema_version": self.landmark_schema_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "max_landmarks": self.max_landmarks,
            "landmarks": [lm.to_dict() for lm in self.landmarks],
        }

    def __len__(self) -> int:
        return len(self.landmarks)

    def __repr__(self) -> str:
        return f"LandmarkSet(id={self.id!r}, n={len(self.landmarks)})"

"""License token handling and feature checks for premium functionality."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Dict, Set

# Mapping of license tokens to the set of enabled features.
LICENSE_FEATURES: Dict[str, Set[str]] = {
    # Example tokens for illustration. Real tokens should be generated securely.
    "PREMIUM-CLUSTER": {"distributed_gpu", "fast_address_db"},
    "PREMIUM-DB": {"fast_address_db"},
}


@dataclass
class PremiumManager:
    """Helper class for validating license tokens and querying features."""

    token: str | None = field(default=None)

    def __post_init__(self) -> None:
        if self.token is None:
            self.token = os.getenv("ALLINKEYS_LICENSE")

    def has_valid_license(self) -> bool:
        """Return True if the current token is recognized."""
        return bool(self.token) and self.token in LICENSE_FEATURES

    def enabled_features(self) -> Set[str]:
        """Return the set of features available for the token."""
        if not self.has_valid_license():
            return set()
        return LICENSE_FEATURES[self.token]

    def distributed_gpu_enabled(self) -> bool:
        """Check if the distributed GPU cluster feature is unlocked."""
        return "distributed_gpu" in self.enabled_features()

    def fast_address_db_enabled(self) -> bool:
        """Check if the faster address database feature is unlocked."""
        return "fast_address_db" in self.enabled_features()

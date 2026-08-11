"""Canonical Capability Hub domain.

This package deliberately separates declarative capabilities from the legacy
runtime tool executor. Importing it never executes package content or performs
network access.
"""

from capabilities.manifests import CapabilityManifest

__all__ = ["CapabilityManifest"]

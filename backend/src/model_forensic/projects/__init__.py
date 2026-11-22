"""Project profiles and the fixed adapter contract: many target projects, one unchanged core (see docs/projects.md)."""

from model_forensic.projects.profile import CONTRACT_VERSION, ProjectProfile, load_profiles, parse_profile

__all__ = ["CONTRACT_VERSION", "ProjectProfile", "load_profiles", "parse_profile"]

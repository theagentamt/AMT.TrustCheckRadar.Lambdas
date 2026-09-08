"""Strict privacy-safe contracts shared by campaign Lambda packages."""

from .app_features import (
    APP_FEATURE_FIELDS,
    APP_FEATURE_MAX_BYTES,
    LANGUAGE_IDS,
    TAXONOMY_BUCKETS,
    AppFeaturesContractError,
    canonical_app_features_json,
    validate_app_features,
)

__all__ = [
    "APP_FEATURE_FIELDS",
    "APP_FEATURE_MAX_BYTES",
    "LANGUAGE_IDS",
    "TAXONOMY_BUCKETS",
    "AppFeaturesContractError",
    "canonical_app_features_json",
    "validate_app_features",
]

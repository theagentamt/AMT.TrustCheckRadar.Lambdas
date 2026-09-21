"""Inactive purchase ownership lifecycle primitives; no provider or activation side effects."""
from .service import OwnershipError, OwnershipStore, token_hash, verified_lineage

__all__ = ["OwnershipError", "OwnershipStore", "token_hash", "verified_lineage"]

"""Trusted Google Play proof normalization; no credentials or client claims accepted."""
from .proof import PlayProof, PlayVerificationError, verify, discover_lineage

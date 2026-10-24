"""Private server-selected application profile, never a wire-client option."""
from .validation import require

PRIMARY = 'ai_primary_v1'


def validate_profile(value):
    require(type(value) is str and value == PRIMARY, 'INPUT_REJECTED')
    return value

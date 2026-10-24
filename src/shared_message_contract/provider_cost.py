"""Server-owned conservative cost bound; no default model, price or allowance."""
from dataclasses import dataclass, asdict
import json
import os
import re
from .runtime import unique_pairs
from .validation import require


@dataclass(frozen=True)
class CostPolicy:
    model: str
    contextTokens: int
    maxOutputTokens: int
    inputNanoUsdPerToken: int
    outputNanoUsdPerToken: int

    def __post_init__(self):
        require(type(self.model) is str and re.fullmatch(r'[A-Za-z0-9._-]+-20[0-9]{2}-[0-9]{2}-[0-9]{2}',self.model), 'PROVIDER_BUDGET_UNAVAILABLE')
        for value,low,high in [(self.contextTokens,1,2_000_000), (self.maxOutputTokens,128,512),
                               (self.inputNanoUsdPerToken,1,1_000_000_000),
                               (self.outputNanoUsdPerToken,1,1_000_000_000)]:
            require(type(value) is int and low<=value<=high,'PROVIDER_BUDGET_UNAVAILABLE')
        require(self.maxOutputTokens<=self.contextTokens,'PROVIDER_BUDGET_UNAVAILABLE')

    @property
    def reservation_nano(self):
        # Full documented model context, including system/schema overhead. This
        # deliberately over-reserves; cached inputs are priced as uncached.
        return self.contextTokens*self.inputNanoUsdPerToken+self.maxOutputTokens*self.outputNanoUsdPerToken

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls,value):
        require(type(value) is dict and set(value)==set(cls.__dataclass_fields__),'PROVIDER_BUDGET_UNAVAILABLE')
        return cls(**value)

    @classmethod
    def from_env(cls):
        try:
            value=json.loads(os.environ['MESSAGE_AI_COST_POLICY'],object_pairs_hook=unique_pairs)
            return cls.from_dict(value)
        except (KeyError,TypeError,ValueError):
            require(False,'PROVIDER_BUDGET_UNAVAILABLE')

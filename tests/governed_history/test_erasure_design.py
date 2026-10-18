"""OFFLINE specification model only; no new mutation wire/store/permission.

These fixtures check intended ordering laws. They are NOT implemented service
acceptance, an approved marker schema, or recognition policy decisions.
"""
from dataclasses import dataclass, field

import pytest


@dataclass
class Specification:
    epochs: dict = field(default_factory=dict)
    admissions: dict = field(default_factory=dict)
    history: dict = field(default_factory=dict)
    accounting: set = field(default_factory=set)
    erased: set = field(default_factory=set)
    operations: dict = field(default_factory=dict)
    recognition: tuple = ("unchanged-existing-progress",)

    def admit(self, owner, check):
        self.admissions.setdefault((owner, check), self.epochs.get(owner, 0))

    def settle(self, owner, check):
        key = (owner, check)
        if key not in self.admissions:
            raise ValueError("NO_ADMISSION")
        self.accounting.add(key)
        if self.admissions[key] == self.epochs.get(owner, 0) and key not in self.erased:
            self.history.setdefault(key, "typed-content-free-result")

    def mutate(self, owner, operation, kind, target=None):
        # An opaque operation identity is a MODEL PARAMETER, not an invented API.
        key, intent = (owner, operation), (kind, target)
        if key in self.operations:
            old, original_response = self.operations[key]
            if old != intent:
                raise ValueError("SAME_OPERATION_DIFFERENT_INTENT")
            return original_response
        if kind == "clear":
            self.epochs[owner] = self.epochs.get(owner, 0) + 1
            self.history = {k: v for k, v in self.history.items() if k[0] != owner}
        elif kind == "delete":
            self.erased.add((owner, target))
            self.history.pop((owner, target), None)
        else:
            raise ValueError("UNSUPPORTED_MODEL_INTENT")
        response = ("MODEL_COMMIT", self.epochs.get(owner, 0))
        self.operations[key] = intent, response
        return response


@pytest.mark.parametrize("order", ["settle_then_clear", "clear_then_settle"])
def test_clear_linearization_never_repopulates_preclear_admission(order):
    model = Specification()
    model.admit("one", "old")
    if order == "settle_then_clear": model.settle("one", "old")
    model.mutate("one", "clear-op", "clear")
    model.settle("one", "old")
    assert model.history == {} and model.accounting == {("one", "old")}
    model.admit("one", "new")
    model.settle("one", "new")
    assert set(model.history) == {("one", "new")}
    assert model.recognition == ("unchanged-existing-progress",)


def test_retry_returns_original_generation_not_a_later_clear_and_changed_intent_conflicts():
    model = Specification()
    original = model.mutate("one", "op", "clear")
    model.mutate("one", "other", "clear")
    assert model.mutate("one", "op", "clear") == original
    assert model.epochs == {"one": 2}
    with pytest.raises(ValueError, match="SAME_OPERATION_DIFFERENT_INTENT"):
        model.mutate("one", "op", "delete", "result")


def test_delete_and_completion_replay_preserve_accounting_and_unaffected_owner():
    model = Specification()
    for owner in ("one", "other"):
        model.admit(owner, "same-visible-id")
        model.settle(owner, "same-visible-id")
    first = model.mutate("one", "op", "delete", "same-visible-id")
    for _ in range(3):
        model.settle("one", "same-visible-id")
        assert model.mutate("one", "op", "delete", "same-visible-id") == first
    assert set(model.history) == {("other", "same-visible-id")}
    assert len(model.accounting) == 2
    assert model.recognition == ("unchanged-existing-progress",)


def test_no_automatic_historical_backfill_after_clear():
    model = Specification()
    model.admit("one", "old")
    model.settle("one", "old")
    model.mutate("one", "op", "clear")
    model.settle("one", "old")
    assert model.history == {}
    assert model.accounting == {("one", "old")}


def test_other_owner_pending_completion_is_not_blocked_by_clear():
    model = Specification()
    model.admit("other", "pending")
    model.mutate("one", "op", "clear")
    model.settle("other", "pending")
    assert set(model.history) == {("other", "pending")}

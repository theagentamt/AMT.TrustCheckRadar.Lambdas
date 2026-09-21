"""Authoritative campaign target locators; no inventory approval writer."""
from .core import (LocatorUnavailable, load_inventory, inventory_condition,
                   locator_for_target, get_owned_locator, locator_put,
                   locator_condition, paired_delete_actions, locator_pointer,
                   validate_locator, owned_page, serialize, deserialize)
from .core import get_locator_by_pointer, InventoryGuardedClient

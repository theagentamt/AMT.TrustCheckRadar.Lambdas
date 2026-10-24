"""Restore only Lambda import aliases, without unloading genuine SDK modules."""
from contextlib import contextmanager
import sys


@contextmanager
def isolated_aliases(*names):
    absent=object()
    previous={name:sys.modules.get(name,absent) for name in names}
    try:
        yield
    finally:
        for name,value in previous.items():
            if value is absent:sys.modules.pop(name,None)
            else:sys.modules[name]=value

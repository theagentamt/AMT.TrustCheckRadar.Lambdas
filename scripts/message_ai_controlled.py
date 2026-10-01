#!/usr/bin/env python3
"""Controlled experiment lifecycle; shipped approval registry is empty."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from evaluation.message_ai.controlled.cli import main

if __name__ == '__main__':
    raise SystemExit(main())

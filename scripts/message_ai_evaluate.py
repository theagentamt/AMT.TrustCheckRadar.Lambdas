#!/usr/bin/env python3
"""Run offline-only qualification preparation; never loads cloud credentials."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from evaluation.message_ai.runner import main

if __name__ == '__main__':
    raise SystemExit(main())

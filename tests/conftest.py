"""Shared pytest config — silence medspaCy/PyRuSH loguru noise + shared helpers."""
from __future__ import annotations

import logging
import os

# Silence the noisy loguru DEBUG output from PyRuSH so test runs are readable.
try:
    from loguru import logger

    logger.disable("PyRuSH")
    logger.disable("medspacy")
except Exception:  # noqa: BLE001
    pass
logging.getLogger("medspacy").setLevel(logging.WARNING)
logging.getLogger("PyRuSH").setLevel(logging.WARNING)

# Force a clean CWD for state isolation; tests that touch the FS use tmp_path.
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

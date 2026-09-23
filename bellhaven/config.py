from __future__ import annotations

import os
from pathlib import Path

BASE_URL = os.getenv(
    "BELLHAVEN_BASE_URL",
    "https://analyst-assessment-production.up.railway.app",
).rstrip("/")
API_BASE = f"{BASE_URL}/api/v1"
TOKEN = os.getenv("BELLHAVEN_API_TOKEN", "")
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "data/review.db"))
DATA_DIR = Path("data")

# Reconciliation scope. These values keep matching logic and CRM notes
# reusable when the target operator changes.
TARGET_PARENT_NAME = os.getenv("TARGET_PARENT_NAME", "Bellhaven Senior Living")
TARGET_PARENT_ACCOUNT_NAME = os.getenv(
    "TARGET_PARENT_ACCOUNT_NAME", f"{TARGET_PARENT_NAME} (Parent Account)"
)
SOURCE_NAME = os.getenv("SOURCE_NAME", f"{TARGET_PARENT_NAME} official website")
SOURCE_BASE_URL = os.getenv("SOURCE_BASE_URL", BASE_URL).rstrip("/")

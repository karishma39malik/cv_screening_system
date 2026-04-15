import hashlib
import uuid
import os
import re
from pathlib import Path
from typing import Optional
import structlog

logger = structlog.get_logger(__name__)


def compute_file_hash(file_bytes: bytes) -> str:
    """SHA-256 hash of file content — used for deduplication."""
    return hashlib.sha256(file_bytes).hexdigest()


def generate_correlation_id() -> str:
    """Unique ID to trace a single CV through the entire pipeline."""
    return str(uuid.uuid4())


def sanitize_filename(filename: str) -> str:
    """Remove dangerous characters from uploaded filenames."""
    # Keep only alphanumeric, dash, underscore, dot
    clean = re.sub(r'[^\w\-_\.]', '_', filename)
    return clean[:255]  # Limit length


def get_file_extension(filename: str) -> str:
    """Extract and lowercase file extension."""
    return Path(filename).suffix.lstrip('.').lower()


def ensure_dir(path: str) -> None:
    """Create directory if it doesn't exist."""
    os.makedirs(path, exist_ok=True)


def truncate_text(text: str, max_chars: int = 8000) -> str:
    """
    Truncate text for LLM input.
    LLMs have context limits — we truncate at word boundaries.
    """
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit(' ', 1)[0]
    return truncated + "\n[TEXT TRUNCATED FOR PROCESSING]"


def format_duration(months: Optional[int]) -> str:
    """Convert months to human-readable string."""
    if not months:
        return "Unknown duration"
    years  = months // 12
    rem_mo = months % 12
    parts  = []
    if years:  parts.append(f"{years} year{'s' if years > 1 else ''}")
    if rem_mo: parts.append(f"{rem_mo} month{'s' if rem_mo > 1 else ''}")
    return " ".join(parts) or "< 1 month"


def mask_email(email: str) -> str:
    """Partially mask email for display (privacy)."""
    if '@' not in email:
        return email
    user, domain = email.split('@', 1)
    masked_user = user[:2] + '*' * (len(user) - 2) if len(user) > 2 else user
    return f"{masked_user}@{domain}"

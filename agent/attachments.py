"""Local-disk attachment storage (Phase D, see ANALYSIS.md's Phase D entry).

Local disk, not a cloud bucket — this project has no real hosted deployment
target today (confirmed by investigation before building: no Render/Railway/
Fly/S3/R2 config exists anywhere in this repo; the docs describe this as a
single Docker Compose deployment). Kept behind this one module, with a
narrow interface (`save`/`path_for`), so that if this project ever gets a
real hosted deployment with an ephemeral filesystem, swapping to a real
bucket later is a contained change to this file, not a rewrite of every
caller.

Only ever referenced from an ESCALATION, never fed into the LLM: this
project has no vision-capable model configured (both Groq models in
agent/llm_client.py are text-only), and a "does this look damaged" judgment
call is exactly the kind of thing this system's escalate-on-uncertainty
design already defers to a human for — see agent/escalation.py's Slack
message building for where the stored attachment is actually surfaced.
"""

from __future__ import annotations

import mimetypes
import os
import uuid
from dataclasses import dataclass

from app.config import ATTACHMENTS_ALLOWED_CONTENT_TYPES, ATTACHMENTS_MAX_BYTES, ATTACHMENTS_STORE_PATH


class AttachmentRejected(Exception):
    """Raised when an upload fails validation (type or size) — the route
    handler turns this into a 400, not a 500; nothing about it is a server
    error."""


@dataclass(frozen=True)
class StoredAttachment:
    attachment_id: str
    filename: str  # the on-disk filename (attachment_id + original extension)
    content_type: str


class AttachmentStore:
    def __init__(self, store_path: str = ATTACHMENTS_STORE_PATH):
        self._store_path = store_path
        os.makedirs(self._store_path, exist_ok=True)

    def save(self, content: bytes, content_type: str) -> StoredAttachment:
        if content_type not in ATTACHMENTS_ALLOWED_CONTENT_TYPES:
            raise AttachmentRejected(f"Unsupported file type: {content_type}")
        if len(content) > ATTACHMENTS_MAX_BYTES:
            raise AttachmentRejected(
                f"File too large ({len(content)} bytes > {ATTACHMENTS_MAX_BYTES} byte limit)."
            )

        attachment_id = str(uuid.uuid4())
        extension = mimetypes.guess_extension(content_type) or ""
        filename = f"{attachment_id}{extension}"
        path = os.path.join(self._store_path, filename)
        with open(path, "wb") as f:
            f.write(content)

        return StoredAttachment(attachment_id=attachment_id, filename=filename, content_type=content_type)

    def path_for(self, attachment_id: str) -> str | None:
        """Returns the on-disk path for `attachment_id`, or None if nothing
        matches — the filename's extension isn't known from the id alone,
        so this looks for any file whose name starts with it."""
        for name in os.listdir(self._store_path):
            if name.startswith(attachment_id):
                return os.path.join(self._store_path, name)
        return None

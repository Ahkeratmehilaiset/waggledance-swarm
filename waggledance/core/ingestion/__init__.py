# SPDX-License-Identifier: BUSL-1.1
"""Universal ingestion — Phase 9 §H.

Brings local files, folders, URLs, vector DBs, streams, and mentor
context packs into the vector_provenance graph. Two modes:

- copy_mode: vectorize-from / ingest a copy into the WD-managed store
- link_mode: keep the source DB external and read it as a linked
  foundational/supportive source
"""

INGESTION_SCHEMA_VERSION = 1

INGESTION_MODES = ("copy_mode", "link_mode", "stream_mode")

SOURCE_KINDS = (
    "local_file",
    "folder",
    "url",
    "faiss_db",
    "stream",
    "mentor_context_pack",
    "synthesized",
)

# Data layout and recovery rules

Keep job directories separate from the code:

```text
job/
  manifest.json        URL/file ID, per-item state, path config, hashes, and errors (private data)
  .lock                Linux advisory lock
  media/<source-name>  Downloaded or locally copied source file
  raw/<name>.txt       Text before punctuation
  txt/<name>.txt       Final transcript
```

## Invariants

1. `inventory` must create a new directory and never overwrite an existing manifest.
2. Path traversal, absolute media paths, backslashes, control characters, and TXT-name collisions
   are rejected.
3. Downloads first land in a temporary directory; only after ffprobe confirms audio and a valid
   positive duration does the file move into `media/`.
4. SHA-256 verification protects existing media and transcripts from being mistaken for safe cache.
5. A batch keeps going after single-item failures, but any failure still makes the command exit
   nonzero and records the error in the manifest.
6. Final TXT is written only after full transcription and any requested punctuation step succeed;
   raw text is kept separately.
7. `status` re-reads and re-verifies hashes instead of trusting historical `done` flags.
8. JSON and TXT are replaced atomically. A crash between artifact and manifest writes can leave an
   untracked file; this is an intentional fail-closed behavior requiring manual inspection or a new
   job rather than automatic deletion.

## Scope and known limitations

- No authenticated login, media upload, scheduler, subtitle-track extraction, SRT, diarization, or
  translation.
- YouTube uses a separate Playwright worker for download, then the same transcription core;
  browser and ASR run sequentially.
- Public Drive folder enumeration is limited by Drive page behavior, quotas, and gdown listing
  limits. `remaining_ok=False` keeps hard failures visible; this does not claim support for
  arbitrarily large folders.
- gdown may normalize filesystem-hostile characters in filenames; inspect the inventory before
  claiming exact preservation of unusual names.
- If a folder changes remotely, create a new inventory; an old manifest does not discover new or
  changed remote files automatically.
- Completed files can be skipped; in-progress chunk state stores counters, not chunk text. Without
  a complete raw TXT, a file restarts from the beginning. Failed downloads also restart from the
  beginning of the file.
- A failed local import batch may be kept for inspection; after fixing input issues, create a new
  job and re-import.
- Raw/final TXT preserve model output exactly; the tool does not automatically remove spaces between
  CJK characters, to avoid corrupting mixed-language content.
- Model config stores snapshot paths only, not hashes of model weights; do not swap models in place
  at the same path and then resume an old job.
- The CLI does not upload audio itself; dependencies or model configuration may still fetch helper
  resources, so total network isolation is not promised.
- Job directories should be trusted and user-exclusive; this is not a multi-tenant sandbox against
  malicious local users.
- There is no automatic cleanup command. If disk space matters, verify final transcripts first and
  request separate authorization before deleting retained media.

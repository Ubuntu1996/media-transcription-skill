# Local validation record

## v0.2 YouTube: real browser download validation

- Installed Playwright 1.62.0 with its Chromium bundle and used a real headless browser.
- Ran `scripts/youtube_transcribe.py` against the public video `jNQXAC9IVRw` with
  `--download-only --site tuberipper`; it exited with code 0.
- Produced a real MP3: `Me at the zoo.mp3`, 304556 bytes, with ffprobe duration 19.032 seconds.
- The manifest recorded `verified_media=1`, `verified_transcripts=0`, and `complete=false`;
  a successful download was not misreported as a finished transcription.
- Re-running the same URL and job reused the verified audio, and the browser conversion attempt
  count still remained one.
- The real OnlyMP3 entrypoint returned HTTP 403 / Cloudflare security verification, so a real
  successful fallback flow is still unverified and cannot be promised.
- Without model configuration, the full entrypoint clearly reported `Missing model_path` before
  submitting the video URL for transcription.
- In addition to old cache paths, user directories, mounted locations, system caches, and temp
  directories were checked for usable FunASR weights. None were found, and no large model was
  downloaded without approval.

## v0.2 automated tests

- Re-ran `.venv/bin/python -m unittest discover -s tests -v`: 30 tests passed.
- Real subprocess regression coverage confirms that after the parent exits, the process group is
  still cleaned up even when descendants ignore SIGTERM.
- `compileall` passed.
- Coverage includes real Playwright routing tests, provider challenges, ad-navigation blocking,
  unsafe download links, video ID / duration validation, structured network errors, single-link
  orchestration, fallback switching, audio reuse, ASR subprocess invocation, and timeout cleanup.
- Browser routing and ASR fixtures are explicit test doubles, not live provider evidence.

## v0.2 independent review

- Specification review passed.
- The logging-mode subprocess cleanup issue found during quality review was fixed and independently
  re-reviewed.
- The reviewer ran all 7 orchestration-layer tests successfully, and the primary runner passed the
  full 30-test suite.
- There are currently no known unresolved code-review blockers.
- Missing model weights and the OnlyMP3 verification wall remain deployment constraints.

The remaining sections below preserve the v0.1 baseline record.

## v0.1 completed locally

- Python 3.12.3: `python -m unittest discover -s tests -v`, 12 tests passed.
- `python -m compileall -q scripts tests`: passed.
- SKILL.md YAML/frontmatter checks passed for name format, description length/punctuation, Linux
  platform declaration, and script paths.
- CLI `--help`, `doctor`, and `status` were run.
- Real FFmpeg/ffprobe were used for temporary WAV files, chunk conversion, and HTML-media rejection
  inside tests.
- An existing Python 3.11.15 ASR environment successfully imported FunASR AutoModel and the
  ModelScope pipeline.
- The lightweight project `.venv` had gdown 5.2.2 installed and did not duplicate large ASR
  dependencies.
- Static script searches found no `shell=True`, `os.system`, `eval`/`exec`, `pickle.load`, GitHub
  token patterns, or hardcoded local user paths.

## Test coverage

Covered behaviors include:

- inventory and original filenames
- relative subfolders
- duplicate-output collisions
- dangerous paths
- Drive URL validation
- count mismatch failures
- download-content validation
- download skipping and tamper protection
- job locking
- real audio chunking
- raw vs final TXT separation
- raw preservation on punctuation failure
- all-empty recognition failure
- edited-final protection
- missing-weight failure
- CLI completion/status verification
- prevention of transcript reuse after changed media

## Independent code review

Two issues found during independent review were fixed and passed re-review:

- When redownloaded media changes, stale raw/TXT must not be reused; transcripts are now bound to
  source-media hashes.
- File/directory ancestor conflicts between source and output paths are rejected early, including
  reverse-overwrite order and case-variant conflicts.

The primary runner and reviewer both re-ran tests: 12 passed, with no remaining logic or security
issues found. Public-folder tests also explicitly assert inventory-only behavior, no cookie usage,
and no waiver of listing limits.

That does not replace real Drive and real model-inference integration testing.

## Incomplete external integration validation

- No real user media was downloaded from a live Drive URL during this validation pass; Drive calls
  were tested with explicit doubles.
- No real model inference or accuracy evaluation was performed during test runs; returned text was
  fixed test data.
- The old workflow's ModelScope cache paths no longer exist; real weights must be relocated or
  obtained with authorization.
- The dependency files have not been claimed to fully recreate a brand-new ASR/punctuation
  environment; a clean reinstall has not yet been validated.

Therefore: the local CLI, file workflow, and skill documentation are built and tested, but a real
Drive -> real speech-recognition end-to-end acceptance run still requires an authorized source and
complete trusted model snapshots.

## Modification scope

This work only built the local project. It did not create a GitHub repository, push code, upload
media, modify Hermes configuration, or install/replace an existing skill. Existing media,
transcripts, and ASR environments were not modified.

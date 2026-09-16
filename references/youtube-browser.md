# YouTube web conversion and automatic transcription

## Observed provider behavior

### TubeRipper (primary provider)

Entry URL: https://tuberipper.cc/74/

Observed real-page flow: fill the video URL into `#videoUrl`, click `#videoBtn`, then wait for the
video title, duration, and Extract Audio link inside `#output`. The YouTube preview link in the
result must match the requested video ID; the workflow must not accidentally download a suggested
homepage video.

Directly clicking Extract Audio can trigger advertising JavaScript first, so waiting only for that
click's download event is unsafe. The validated approach is to read the generated HTTPS TubeRipper
download link, open it in a new page within the same clean browser context, and capture the real
Playwright download event.

Temporary download URLs are signed. Do not hardcode them, commit them, or reuse them across jobs;
read them fresh from the actual page each time. Navigation must stay within the allowed provider
domains, ad popups must be closed, and unknown redirects must be rejected.

Previous live short-video validation artifact: MP3, 304556 bytes, ffprobe duration 19.032 seconds.
That validates the download path, not transcription accuracy; independent run details belong in
`VALIDATION.md`.

### OnlyMP3 (restricted fallback)

Entry URL: https://en.onlymp3.to/converter-v2?utm_source=convert-more

On this machine the live site returned HTTP 403 with Cloudflare `Just a moment...` / security
verification. That is an access blocker, not a successful conversion. The code detects the
challenge and reports it without simulating a human check, using CAPTCHA-solving services, or
refreshing repeatedly.

The fallback adapter operates only when the page is accessible and the form plus trusted result
link can be identified uniquely. If the layout is unclear or multiple Download candidates appear,
it stops. A real successful conversion path for this provider is still unverified.

## One-time configuration and routine runs

Run in the project directory through Hermes `terminal`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/youtube_transcribe.py --configure --asr-python '<ASR_PYTHON>' --model-path '<TRUSTED_SNAPSHOT>'
.venv/bin/python scripts/youtube_transcribe.py 'https://www.youtube.com/watch?v=VIDEO_ID'
```

Only the browser Python needs Playwright. FunASR is launched through the separate interpreter saved
in config. The browser exits before ASR starts to avoid unnecessary concurrent memory usage. Do not
call `resolve()` on the virtualenv Python and then execute that resolved system path, because that
can lose the virtualenv package set.

Config path priority is: explicit `--config`, then the `TRANSCRIPTION_CONFIG` environment variable,
then the project's `config.local.json`. Command-line options can override config values.
`config.example.json` is a template, not proof that the current machine is already configured.
Snapshots must contain real weights and matching config/token assets, not just an empty directory.

If models are missing, the workflow fails first and does not silently download a 936 MB weight file.
After user approval, obtain a trusted snapshot once and configure it. Before approval, code updates
or explicit audio download are fine, but do not claim the full end-to-end pipeline is deployed.

## Job state and exit behavior

Default job directory: `~/transcription_jobs/youtube-VIDEO_ID/`.

- The manifest stores the exact source, video ID, original title, uploader and `publish_date`
  (`YYYY-MM-DD`) when available, actual filename, provider attempts, and SHA-256 hashes.
- `media/` holds the verified MP3, `raw/` the raw recognition, and `txt/` the final transcript.
- Filenames come from YouTube metadata when available in the form
  `[uploader] [YYYY-MM-DD] video title`, rather than provider branding suffixes. The date is the
  source's publication date, retaining its calendar day without timezone conversion. Missing
  uploader/date fields are omitted independently; neither upload time nor the current date is
  substituted for an unavailable publication date.
- Title/uploader come from YouTube oEmbed. Since oEmbed does not expose publication dates, a
  separate bounded public watch-page lookup reads `datePublished` metadata or the embedded player
  microformat's `publishDate`. Lookup failures and access gates do not block the pipeline or
  discard metadata from the other lookup; no cookies or challenge bypass are used.
- Filesystem-hostile characters are replaced with underscores, while the original title stays in
  the manifest. Overlong titles require an explicit `--title`; they are not silently truncated.
  `--title` overrides the full filename title and skips automatic uploader/date additions.
- Completed audio is not reconverted; verified finished transcripts are skipped by the core.
  Existing jobs retain their recorded filenames even if publication metadata later becomes
  available or changes. Use a new `--job` directory for the new naming scheme. Manual edits are
  not overwritten.
- Different YouTube share URL forms are allowed, but they must resolve to the same valid video ID;
  the original URL string is preserved.
- If reconverted audio bytes differ, old transcripts must not be reused. Create a new job instead
  of silently replacing the source behind an existing transcript.
- A single-job lock is enforced. Browser timeouts stop the whole worker process group; ASR has an
  independent timeout and writes logs to `asr.log`.
- `--download-only` may return 0 while `complete=false` still means there is no full transcript.
- The maximum-audio-size check happens after download completion; it is not a streaming bandwidth
  quota, so confirm disk space in advance.

## Validation and security boundary

Run `python -m unittest discover -s tests -v` through `terminal`.
Browser tests use real Playwright with explicit routed pages/audio and do not count as live-site
proof. The orchestration layer uses explicit ASR doubles; real FunASR still needs complete models
and real speech for acceptance.

Do not allow extension installs, executable download prompts, notification permissions, payments,
or cookie/login transfer. Submit only the user-authorized video URL to the chosen third-party
converter and do not upload local audio to an ASR API. Provider availability can change at any
time; preserve real errors instead of treating error HTML as audio or pretending a transcript was
produced.

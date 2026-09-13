# Environment and models

## Lightweight download environment

Run in the project directory through the Hermes `terminal` tool:

```text
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/media_transcribe.py doctor
```

`ffmpeg` and `ffprobe` must be available in `PATH`. On Ubuntu, after user approval for system
packages, you can run `sudo apt-get update` and `sudo apt-get install ffmpeg`.
Python 3.11 is recommended; the CLI and offline tests were also validated on Python 3.12.

## Prefer reusing an existing ASR environment

Verify the selected interpreter with:
`<ASR_PYTHON> -c "from funasr import AutoModel; print('import OK')"`.

If punctuation is needed, also verify:
`<ASR_PYTHON> -c "from modelscope.pipelines import pipeline; print('import OK')"`.

Download and ASR steps may use separate Python environments and hand off through the same job
folder. There is no need to duplicate models or modify Hermes's own Python environment.

## Fresh ASR environment (optional, needs extra disk and network)

Do not automatically download multiple gigabytes when dependencies are missing. First confirm RAM,
free disk, and user intent.

In the chosen virtual environment, run:

```text
python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-asr.txt
python -m pip check
```

`torch` and `torchaudio` must be a compatible pair. Do not blindly copy mismatched versions from an
older environment. The FunASR/ModelScope versions in `requirements-asr.txt` were taken from an
existing local environment; they are not guaranteed to recreate successfully on every new platform,
so you should still run imports and a real short-audio test afterward.

Punctuation needs additional dependencies:
`python -m pip install -r requirements-punctuation.txt`, then verify the pipeline import.
Those extra dependencies came from earlier recovery notes and are not a fully validated lock file.

## Local model snapshots

The CLI does not accept remote model aliases. Pass a trusted snapshot directory containing a real
`model.pt`, config files, token files, and other required assets. An empty directory or only
`model.pt.incomplete` does not count as ready. Model licensing is independent from this project.

Previously used model IDs:
- ASR: `iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch`
- VAD: `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch`
- Chinese punctuation: `iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch`

Obtain model weights from a trusted publisher and verify them separately; this project does not
ship weights or an automatic model-download script.

Do a real short-audio transcription test before scaling to bigger jobs. Fixed-size chunking can
split words across boundaries. CPU chunking reduces working audio memory, but not the memory needed
for the model weights themselves. If the process is killed for low memory, reduce concurrency
instead of repeatedly restarting the same large job.

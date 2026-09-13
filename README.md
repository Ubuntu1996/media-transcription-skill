# YouTube / Google Drive / 本地媒体 → TXT 转录 Skill

本地项目，尚未发布 GitHub。包含 Hermes skill 文档和可独立运行的 Python CLI。
主流程：YouTube 链接 → Playwright 网页转 MP3 → 本地 FunASR → TXT。
原来的公开 Drive 和本地媒体批处理流程保留。

## 当前范围

- YouTube 单链接自动流程：TubeRipper 优先，失败后尝试 OnlyMP3（目前受验证墙阻挡）。
- 公开 Drive 单文件、文件夹（含相对子目录）；也可导入本地媒体。
- 保留中文、空格和常规原始文件名；扩展名换成 `.txt`。
- 文件级恢复、SHA-256 校验、原子进度文件、单任务锁。
- 不覆盖人工修改的成稿；补标点失败保留原始文字。
- 一次只解码一段 16 kHz 单声道音频，默认 60 秒、2 个 CPU 线程。
- 不自动上传、不自动清理、不创建定时任务、不读取浏览器 Cookie。
- 当前支持 Linux、Python 3.11+；不是 SRT/时间轴字幕工具。

## 项目结构

```text
SKILL.md                         Hermes 的 skill 入口
scripts/youtube_transcribe.py    一条 YouTube 链接到本地 TXT 的自动入口
scripts/youtube_browser.py       Playwright 网页下载子进程
scripts/media_transcribe.py      Drive / 本地媒体 / 转录核心
references/setup.md             环境、模型与迁移说明
references/design.md            数据布局、恢复及限制
requirements.txt                gdown + Playwright（不含大型 ASR 包）
config.example.json             一次性配置示例；本机配置不要上传
requirements-asr.txt            可选 ASR 依赖
requirements-punctuation.txt    可选标点依赖
tests/test_workflow.py           离线回归测试
VALIDATION.md                    本次实际验证记录
```

## 快速开始

在项目目录运行。下面的 URL、文件名和模型路径是占位示例，须自行替换。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/media_transcribe.py doctor
.venv/bin/python scripts/media_transcribe.py --help
```

确保系统有 `ffmpeg` 和 `ffprobe`。ASR 是可选重依赖，安装前先看
[环境与模型说明](references/setup.md)，可直接使用已有 ASR Python 环境，
不必在项目 `.venv` 再安装一套 PyTorch。

### YouTube：配置一次，以后只传链接

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/youtube_transcribe.py --configure \
  --asr-python "$HOME/venvs/funasr-cpu/bin/python" \
  --model-path '/path/to/complete/trusted/model-snapshot'
.venv/bin/python scripts/youtube_transcribe.py 'https://www.youtube.com/watch?v=VIDEO_ID'
```

`--configure` 检查模型文件和 Python 导入后，把本机设置写入 `config.local.json`。
此文件已经加入忽略规则。若模型尚未准备好，不会擅自下载大文件，也不会先
把视频 URL 交给转换站；可以显式用 `--download-only` 先保存音频。

程序自动执行：

1. 解析 YouTube URL、定位任务目录，检查已有音频/成稿是否可复用。
2. 用独立 Playwright Chromium 打开转换网站、填写链接、等待转换结果。
3. TubeRipper 优先；失败后尝试 OnlyMP3，验证码/权限限制不会自动绕过。
4. 读取实际页面生成的音频链接，用浏览器下载事件保存 MP3；不是调用猜测的私有 API。
5. 校验真实音轨/格式，按视频标题命名，自动调用已配置的 FunASR Python。
6. 输出 `~/transcription_jobs/youtube-VIDEO_ID/txt/视频标题.txt`。

```bash
# 显式只下载，不做 ASR；不需要已有模型
.venv/bin/python scripts/youtube_transcribe.py 'YOUTUBE_URL' --download-only
# 固定站点或任务目录（通常不需要）
.venv/bin/python scripts/youtube_transcribe.py 'YOUTUBE_URL' --site tuberipper --job '/path/to/job'
```

网站会收到视频 URL；不要将保密的非公开视频链接交给第三方转换站。
网页广告不会被当作有效下载，不安装扩展、不接受通知、不付费、不解验证码。
详见 [YouTube 网页自动化](references/youtube-browser.md)。

### 公开文件夹

```bash
.venv/bin/python scripts/media_transcribe.py inventory \
  --url 'https://drive.google.com/drive/folders/FOLDER_ID' \
  --job "$HOME/transcription_jobs/new-batch" --expected-count 5
.venv/bin/python scripts/media_transcribe.py download \
  --job "$HOME/transcription_jobs/new-batch"
```

`--expected-count` 指支持的音视频数量，不包括其他文档。不知道数量时可省略，
但必须人工核对清单，不能把 gdown 未报错当作“肯定没有遗漏”。

### 公开单文件

```bash
.venv/bin/python scripts/media_transcribe.py inventory \
  --url 'https://drive.google.com/file/d/FILE_ID/view' \
  --name '原始视频名称.mp4' --expected-count 1 \
  --job "$HOME/transcription_jobs/single-video"
.venv/bin/python scripts/media_transcribe.py download \
  --job "$HOME/transcription_jobs/single-video"
```

单文件必须提供核实过的原文件名；不会自动改成 source.mp4。

### 已下载的本地文件

```bash
.venv/bin/python scripts/media_transcribe.py local \
  --input '/path/to/media' --job "$HOME/transcription_jobs/local-batch"
```

该命令会复制媒体，不会移动或删除原文件。任务目录必须不存在。

### 转录与补标点

```bash
/path/to/asr-env/bin/python scripts/media_transcribe.py transcribe \
  --job "$HOME/transcription_jobs/new-batch" \
  --model-path '/path/to/trusted/asr-snapshot' \
  --vad-path '/path/to/trusted/vad-snapshot' \
  --punc-path '/path/to/trusted/punctuation-snapshot' \
  --threads 2 --chunk-seconds 60
.venv/bin/python scripts/media_transcribe.py status \
  --job "$HOME/transcription_jobs/new-batch"
```

没有 VAD 或标点模型时，删除对应选项。选择不加标点属于有效运行；明确要求
补标点但失败时，返回错误并保留 `raw/`，不会冒充完整成功。

最终成稿：`<JOB>/txt/原始视频名称.txt`。
原始识别：`<JOB>/raw/原始视频名称.txt`。
进度和错误：`<JOB>/manifest.json`。

重复 `download` 或 `transcribe` 会检查已完成文件并重试失败项。
恢复单位是整文件，不是字节级下载或 ASR 分块。更换模型、设置或成稿被修改时，
请建新任务，避免误覆盖。`status` 只有全部源媒体和成稿校验通过才返回 0；
下载成功但尚未转录时，`download` 返回 0，而 `status` 返回 1，这是预期行为。

## 作为 Hermes Skill 使用

当前只构建本地项目，不改 Hermes 配置、不覆盖原有 `media-transcription`。
现在可让 Hermes 读取本项目的 `SKILL.md` 后执行流程。
需要正式安装时，将 `SKILL.md`、`scripts/`、`references/`、`requirements*.txt` 和
`config.example.json` 一起放入当前 profile 的
`skills/media-transcription/`，并在新会话加载。不要只复制 SKILL.md 而漏掉脚本。
多 profile 环境使用实际 `HERMES_HOME`，不要误装到其他 profile。

官方说明：https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

测试真实调用 FFmpeg/ffprobe；Drive 和 ASR/标点返回值是明确标注的测试替身，
不代表真实下载或真实语音识别效果。实际覆盖与未验证项见 `VALIDATION.md`。

## 安全和发布

- 只处理自己拥有或获准处理的文件；访问受限时停下，不绕过权限。
- 项目不保存真实 Drive 链接、用户成稿或凭据。
- 默认 `.gitignore` 排除任务、媒体、模型、虚拟环境、Cookie 和密钥文件。
  上传前仍应人工检查文件清单，不能只依赖忽略规则。
- 目前未选定开源许可证，按私有项目保留权利；以后公开前确认作者及许可证。

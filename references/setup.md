# 环境与模型

## 轻量下载环境

通过 Hermes `terminal` 在项目目录执行：

```text
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/media_transcribe.py doctor
```

ffmpeg/ffprobe 必须在 PATH。Ubuntu 可在用户允许安装系统包后，通过
`terminal` 执行 `sudo apt-get update` 和 `sudo apt-get install ffmpeg`。
Python 推荐 3.11；CLI/离线测试也在 Python 3.12 上执行验证。

## 优先复用现有 ASR 环境

通过 `terminal` 检查所选解释器：
`<ASR_PYTHON> -c "from funasr import AutoModel; print('import OK')"`。
标点另需验证：
`<ASR_PYTHON> -c "from modelscope.pipelines import pipeline; print('import OK')"`。

下载与 ASR 可分别使用不同 Python 环境；靠同一个任务目录交接，不需要重复
下载、复制模型或者修改 Hermes 自己的 Python 环境。

## 全新 ASR 环境（可选，需额外磁盘和网络）

不要因缺依赖就自动下载数 GB。先核对 RAM、空闲磁盘和用户意图。
在用户选定的虚拟环境中，通过 `terminal` 执行：

```text
python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-asr.txt
python -m pip check
```

torch 与 torchaudio 应选择相互兼容的配对版本。不要盲目复制旧环境中不同
版本的二者。requirements-asr.txt 的 FunASR/ModelScope 版本来自本机现有
环境；没有承诺在任意新平台上重建成功，安装后仍要做导入和真实短音频测试。

补标点需要额外依赖：通过 `terminal` 执行
`python -m pip install -r requirements-punctuation.txt`，随后验证 pipeline 导入。
这些附加依赖来自旧流程的缺依赖恢复记录，并非已验证的全量锁文件。

## 本地模型快照

CLI 不接受远程模型别名；传包含真实 `model.pt`、配置和词表等资源的可信快照
目录。仅存在目录或 `model.pt.incomplete` 不算准备好。模型自身的许可证独立于项目。

原流程使用过：
- ASR：`iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch`
- VAD：`iic/speech_fsmn_vad_zh-cn-16k-common-pytorch`
- 中文标点：`iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch`

模型需要另行从可信发布者获取并核验；本项目不附带权重或自动下载脚本。
不要把本机缓存路径、旧任务 ID 写入共享 skill。

先识别一段真实、有说话声的短音频，检查内容，再扩大到批量任务。
固定分段可能切断词句；CPU 分块降低音频工作内存，但不减少模型权重本身。
如果进程因内存不足被杀，降低并发，不要不断重启同一大任务。

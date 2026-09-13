# 本地验证记录

## v0.2 YouTube：真实网页下载验证

- 已安装 Playwright 1.62.0 及配套 Chromium；使用真实无头浏览器。
- 实际运行 `scripts/youtube_transcribe.py`，输入公开视频 `jNQXAC9IVRw`，使用
  `--download-only --site tuberipper`，成功返回退出码 0。
- 实际 MP3：`Me at the zoo.mp3`，304556 字节，ffprobe 时长 19.032 秒。
- 清单记录 `verified_media=1`、`verified_transcripts=0`、`complete=false`；
  没有把成功下载宣称为已完成转录。
- 重复执行相同 URL/任务，复用已校验音频，浏览器转换尝试仍只有一次。
- OnlyMP3 的真实入口返回 HTTP 403 / Cloudflare security verification，
  因此真实成功转换流程仍未验证，不能承诺它一定可作备用。
- 无模型配置时，完整入口在提交视频前明确报 `Missing model_path`，未假装识别。
- 除旧缓存路径外，也检查了用户目录、挂载位置、系统缓存和临时目录，
  未找到可用 FunASR 权重；没有收到下载模型的确认，所以没有下载大模型。

## v0.2 自动化测试

主执行者重新运行 `.venv/bin/python -m unittest discover -s tests -v`：30 项测试通过。
另有真实子进程回归测试，确认主进程退出后仍会清理忽略 SIGTERM 的同组后代进程。
`compileall` 通过。新增覆盖真实 Playwright 路由测试、站点挑战、广告导航拦截、
危险下载链接、视频 ID/时长校验、结构化网络错误、单链接协调、备用站切换、
音频复用、ASR 子进程调用和超时终止。浏览器路由与 ASR 测试数据均明确标注为测试替身。

## v0.2 独立复审

规格审查通过；质量审查发现的日志模式子进程清理问题已修复并通过独立复审。
复审者运行协调层 7 项测试全部通过，主执行者运行全套 30 项测试通过。
目前没有已知的未解决代码审查阻碍。模型权重缺失和 OnlyMP3 验证墙仍是实际部署限制。

以下为 v0.1 的基线记录。

## v0.1 已实际执行

- Python 3.12.3：`python -m unittest discover -s tests -v`，12 项测试通过。
- `python -m compileall -q scripts tests`：通过。
- SKILL.md YAML/frontmatter：名称格式、描述长度/句号、Linux 平台和脚本路径检查通过。
- CLI `--help`、`doctor`、`status` 已运行。
- 真实 FFmpeg/ffprobe：用于测试中的临时 WAV、分段转换和假 HTML 媒体拒绝。
- 原有 Python 3.11.15 ASR 环境：FunASR AutoModel、ModelScope pipeline 均实际导入成功。
- 项目轻量 `.venv`：gdown 5.2.2 已安装；未复制或安装大型 ASR 依赖。
- 脚本静态搜索：未发现 shell=True、os.system、eval/exec、pickle.load、GitHub token 模式或硬编码本机用户路径。

## 测试覆盖

清单和原文件名、相对子目录、重名冲突、危险路径、Drive URL 校验、数量不匹配、
下载内容验证、下载跳过/改动保护、任务锁、真实音频分段、TXT/原始文本分离、
标点失败保留 raw、识别全空失败、成稿修改保护、缺失权重失败、CLI 完成状态检查、
重新下载不同媒体后不得复用旧文字稿。

## 独立代码审查

独立审查发现的两项问题均已修复，并通过复审：

- 重新下载的媒体发生变化时，不得复用旧 raw/TXT；现将文字稿绑定源媒体哈希。
- 输出和源路径的文件／目录祖先冲突必须提前拒绝，覆盖顺序反转和大小写变体。

主执行者与复审者均重新运行测试：12 项通过，未发现剩余逻辑或安全问题。
公开文件夹测试还明确断言仅枚举、不读取 Cookie、不忽略列表上限。
这不替代真实 Drive 和模型推理集成测试。

## 未完成的外部集成验证

- 本次未使用真实 Drive URL 下载用户媒体；Drive 调用通过明确的测试替身测试。
- 本次未运行真实模型推理或评估识别准确率；测试返回文本是明确的固定测试数据。
- 旧流程记录的 ModelScope 缓存路径当前不存在；实际权重需重新定位或按用户授权获取。
- 没有声称全新 ASR/标点环境可由依赖文件完全复现；尚未做干净环境重装验证。

因此：本地 CLI、文件流程、skill 文档已构建并测试；真实 Drive → 真实语音识别的
端到端验收仍需一个获准使用的来源和完整可信模型快照。

## 修改范围

只构建本地项目；未创建 GitHub 仓库、推送、上传素材，未修改 Hermes 配置或
安装/替换已有 skill。既有媒体、文字稿、ASR 环境没有被修改。

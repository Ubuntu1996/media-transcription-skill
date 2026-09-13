# YouTube 网页转换与自动转录

## 已观察到的站点行为

### TubeRipper（主站）

入口：https://tuberipper.cc/74/

真实页面流程：`#videoUrl` 填入视频链接，点击 `#videoBtn`，等待
`#output` 中的视频标题、时长与 Extract Audio 链接。结果里的 YouTube 预览链接
应与输入视频 ID 一致，不能误下载首页推荐视频。

直接点击 Extract Audio 的 JavaScript 会先弹广告，不能只等待这个点击的 download
事件。已实际成功的方式是读取其生成的 HTTPS TubeRipper 下载链接，在同一干净
浏览器 context 内的新页访问链接，并捕获 Playwright 下载事件。

临时下载链接中有签名，不能硬编码、复制进仓库或跨任务重用；每次需要时重新读取
实际页面。导航必须限定在允许的站点域名内，关闭广告弹窗，拒绝不明跳转。

先前的真实短视频验证产物：MP3，304556 字节，ffprobe 时长 19.032 秒。
这是下载功能验证，不是识别准确率测试；独立运行结果写入 VALIDATION.md。

### OnlyMP3（受限备用站）

入口：https://en.onlymp3.to/converter-v2?utm_source=convert-more

本机实测 HTTP 403，页面为 Cloudflare 的 `Just a moment...` / security verification。
这是访问阻碍，不是转换成功。代码识别挑战并报告，不模拟人工验证、不使用验证码
破解服务、不反复刷新。

备用适配器只在页面可正常访问、表单和可信结果链接能唯一识别时操作；布局不明或
多个 Download 候选时停止。该站的真实成功转换路径尚未验证，不能保证备用可用。

## 一次性配置与日常自动运行

通过 Hermes `terminal` 在项目目录运行：

```text
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/youtube_transcribe.py --configure --asr-python '<ASR_PYTHON>' --model-path '<TRUSTED_SNAPSHOT>'
.venv/bin/python scripts/youtube_transcribe.py '<YOUTUBE_URL>'
```

只有浏览器 Python 需要 Playwright；FunASR 由配置里的另一个解释器启动。浏览器
退出后才运行 ASR，避免同时占用大量内存。不要对虚拟环境 Python 调用 `resolve()`
再作为命令执行：这会跟随符号链接回到系统 Python，丢失虚拟环境依赖。

配置路径优先级：显式 `--config`、`TRANSCRIPTION_CONFIG` 环境变量、项目内
`config.local.json`。命令行选项可覆盖配置。`config.example.json` 是模板，不是
已经在本机配置成功的证明。快照需包含真实权重和配套配置/词表，不能只建空目录。

模型缺失时先报错，不自动下载 936 MB 权重；用户确认后再获取可信快照，配置一次。
在未收到确认前可以完善代码或显式下载音频，但不能声称全链路已部署完毕。

## 任务状态与退出行为

默认任务目录：`~/transcription_jobs/youtube-VIDEO_ID/`。

- 保存准确来源、视频 ID、原始标题、实际文件名、转换站尝试和 SHA-256。
- `media/` 是校验后的 MP3；`raw/` 是原始识别；`txt/` 是最终文字稿。
- 文件名取视频标题，不沿用转换站的品牌后缀；不适合文件系统的字符换成下划线，
  原始标题保留在清单中。过长标题需显式提供 `--title`，不暗自截断。
- 完成的音频不会再次转换；现有成稿校验后由核心跳过。人工修改不会被覆盖。
- 源 URL 可用不同 YouTube 分享形式，但必须是同一个有效视频 ID；原链接保留。
- 重新转换后字节变化，不得复用旧文字稿；建新任务，而不是偷偷替换成稿来源。
- 有单任务锁。浏览器超时会终止整个子进程组；ASR 有独立超时，日志写入 `asr.log`。
- `--download-only` 成功返回 0，但报告中的 `complete=false` 表示没有完整文字稿。
- 最大音频大小在下载完成后检查，不是流量硬配额；提前确认磁盘空间。

## 验证与安全边界

用 `terminal` 执行 `python -m unittest discover -s tests -v`。
浏览器测试使用真实 Playwright 配合明确的路由测试页面/音频，不当作真实网站结果。
协调层使用明确的 ASR 测试替身；真实 FunASR 仍需完整模型和真实语音做验收。

不能允许安装扩展、执行下载的程序、通知授权、支付或 Cookie/登录信息传递。
只向用户指定的第三方转换服务提交获准处理的视频 URL，不上传本地音频给识别 API。
网址/站点可用性随时变化；每次失败保留真实错误，不把错误 HTML 当音频或伪造成稿。

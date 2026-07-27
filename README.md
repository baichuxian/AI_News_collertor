# AI_News_collertor
一个支持多源抓取和四语言翻译的 AI 新闻聚合工具，让你轻松掌握全球 AI 动态。

# AI News Aggregator

专业、稳健的 AI 新闻聚合器（requests + BeautifulSoup），支持多语言翻译、24 小时时间过滤、列表页解析与 RSS。

功能简介
- 支持来源配置化（sources.json），可扩展任意网站
- 优先用 BeautifulSoup 对列表页解析；对动态站点提供 best-effort / 注释建议（Selenium 可选）
- 输出标题、发布时间、摘要、原文链接、正文（若能抓取）、首张配图（若能抓取）
- 支持翻译：简体中文(zh-CN)、繁体中文(zh-TW)、英语(en)、日语(ja)
- 进度条（tqdm）、控制台彩色输出、错误隔离（某源失败不影响其它源）

安装
```powershell
py -m pip install -r requirements.txt
```

运行示例
```
py ai_news_crawler.py --hours 24 --limit 5 --translate --lang zh-CN --output news.json
py ai_news_crawler.py --source "DeepSeek" --limit 3 --translate --translate-to ja --output news.md
py ai_news_crawler.py --list-sources
```

命令行参数
- `--list-sources`：列出 sources.json 中的所有源并退出
- `--source NAME`：仅抓取指定源（可多次），若不指定则抓取全部源
- `--limit N`：每个源最多抓取 N 条（默认 5）
- `--hours N`：只包含过去 N 小时内的文章（默认 24）
- `--translate`：开启翻译
- `--lang`：目标语言（zh-CN/zh-TW/en/ja），默认 zh-CN
- `--translate-to`：旧参数（优先级最高）——同上
- `--output PATH`：保存输出（.json 或 .md）。若不指定，则打印到控制台

支持语言
- 简体中文：zh-CN
- 繁体中文：zh-TW
- 英语：en
- 日语：ja

新闻源（sources.json）包含：
- DeepSeek, Kimi (月之暗面), 豆包 (Doubao), 通义千问 (Qwen), xAI (Grok), Google Gemini, Perplexity AI
- 以及基础 RSS 源：MIT Technology Review AI, VentureBeat AI, TechCrunch AI, AI Trends, ArXiv cs.AI, OpenAI, DeepMind

注意事项与建议
- 某些站点为 SPA/JS 渲染（例如部分动态更新页、或通过脚本载入），静态请求可能无法抓到完整列表或正文。若需要稳定抓取这类站点，建议后续引入 Selenium 或 Playwright。
- 翻译使用第三方无密钥翻译器，可能受限频率或网络限制，翻译失败将回退到原文并打印警告。脚本会在 JSON 中写入 `translation_error` 字段以便排查具体错误。
- 若需要通过代理抓国外源，请在系统环境里设置 `HTTP_PROXY` / `HTTPS_PROXY`，requests 会自动识别。
- Windows 用户如遇 SSL 证书错误，可设置环境变量或使用代理。

日志与容错
- 每个源的抓取都被 try/except 包裹，单源错误不会中断程序
- 控制台使用颜色提示：成功（绿色）、警告（黄色）、错误（红色）

日志位置与格式
- 翻译失败的完整 traceback 会被记录到项目目录下的 logs/ 目录。
  - 人类可读日志： logs/translation_errors.log （按时间追加，包含时间、来源、链接、字段、原文与完整堆栈）
  - 结构化日志： logs/translation_errors.jsonl （每行一条 JSON，便于脚本/ELK/分析工具消费；字段：time, source, link, field, original, traceback）
- 注意：news.json / news.md 中只保留简短的错误摘要（translation_error 字段），完整 traceback 仅写入 logs/ 下的文件以保持输出文件清爽。

查看日志（示例命令）
- 在类 Unix 系统下：
  - 实时查看（文本日志）：
    tail -n 100 logs/translation_errors.log
  - 快速筛选最近条目（JSONL）：
    tail -n 200 logs/translation_errors.jsonl | jq -c '. | {time,source,field}'
- 在 Windows PowerShell 中：
  - 实时查看文本日志：
    Get-Content .\logs\translation_errors.log -Tail 100 -Wait
  - 读取 JSONL（逐行解析）：
    Get-Content .\logs\translation_errors.jsonl | ForEach-Object { $_ | ConvertFrom-Json | Select-Object time,source,field }

.gitignore 提示
- logs/ 与 logs/translation_errors.log 已加入 .gitignore（避免将敏感或尺寸较大的堆栈日志提交到版本库）。

日志管理建议
- 如果运行频率较高，建议定期轮转或压缩 logs/ 目录（例如按天归档），并保留一定期限的结构化日志以便审计与分析。

Windows 注意事项
- 脚本使用 colorama 来确保彩色输出在 Windows 控制台上兼容。已在 requirements.txt 中包含 colorama。
- 若在某些旧版 Windows 控制台看到异常颜色或转义字符，建议使用 Windows Terminal 或在 PowerShell 中运行，或安装/升级 colorama。

开发者
- 这是一个可扩展的脚本；可根据需要往 `sources.json` 添加自定义源，并为特殊结构站点新增专用解析函数。

示例：输出与日志片段

下面给出运行后可能生成的示例片段，帮助理解 news.json（或 news.md）与 logs/translation_errors.jsonl 的字段含义。

1) news.json（items 数组中的一条示例）

```json
{
  "source": "DeepSeek",
  "source_url": "https://deepseek.com/news",
  "title": "New breakthroughs in multimodal models",
  "title_translated": "多模态模型的新突破",
  "link": "https://deepseek.com/news/12345",
  "published": "2026-07-27",
  "published_parsed": "2026-07-27T12:34:00+00:00",
  "summary": "A short summary in English...",
  "summary_translated": "一段简短的中文摘要...",
  "body": "Full article body text (truncated)...",
  "image_url": "https://deepseek.com/media/image1.jpg",
  "translation_ok": true,
  "translated_to": "zh-CN",
  "translation_error": null
}
```

2) logs/translation_errors.jsonl（结构化日志，每行一个 JSON）示例行

```json
{"time":"2026-07-27T12:40:00+00:00","source":"Perplexity AI","link":"https://blog.perplexity.ai/post/xyz","field":"title","translated_to":"zh-CN","original":"An example title that failed to translate","traceback":"Traceback (most recent call last):\n  ...\nRuntimeError: translation service unavailable"}
```

说明：
- news.json 中的 `translation_error` 字段仅包含短摘要（例如最后一行错误信息）或 null，便于人工阅读与后续处理。
- logs/translation_errors.jsonl 保存完整 traceback 以便自动化分析或导入 ELK 等系统（每行一个 JSON 对象）。
- 当你在自己的网络环境触发翻译错误时，logs/ 下会同时生成 human-readable 的 translation_errors.log 以及结构化的 translation_errors.jsonl，便于双重查看与分析。

# RSS AI Report

一个用于解析 RSS 期刊论文订阅，并使用大语言模型批量生成文献阅读报告的小工具。

本目录是在原项目 `cyclinbox/RSS-report-with-AI-process` 基础上修改得到的版本，主要变化包括：

- 将话题、关键词、输出语言、LLM 配置和邮件配置外置到 `config.yaml`。
- 增加反向关键词 masking，避免 `imaging`、`managing` 等词被误判为 `aging`。
- 简化 `libqwen.py`，只保留核心的 HTTP JSON 调用。
- 检测 Pandoc，并在可用时额外输出 HTML 报告。
- 增加邮件发送能力，可在 YAML 中开关并配置收件人。

## 依赖库

本项目基于 Python 3 编写，需要以下第三方库：

| 依赖项 | 用途 |
| --- | --- |
| `pandas` | 生成 Parquet 和 Excel 报告 |
| `numpy` | `pandas` 的上层依赖 |
| `requests` | 获取 RSS、论文全文和调用 LLM |
| `bs4` / `beautifulsoup4` | 网页内容解析 |
| `feedparser` | RSS 订阅源解析 |
| `PyYAML` | 读取外部 YAML 配置 |

邮件发送使用 Python 标准库 `smtplib`、`email`；Pandoc 检测和调用使用标准库 `subprocess`、`shutil`。

如果希望输出 HTML 版报告，还需要在系统中安装 Pandoc。

## 快速开始

1. 将本项目目录复制到本地，并进入该目录。
2. 准备 OPML 订阅源文件，例如本目录提供的 `feed-rss-list.opml`。
3. 根据自身情况修改 `config.yaml`。
4. 运行主程序：

```bash
python parse_opml-with-AI-process.py -c config.yaml
```

也可以覆盖配置中的 OPML 路径或时间范围：

```bash
python parse_opml-with-AI-process.py -c config.yaml -f feed-rss-list.opml -d 8
```

程序会自动抓取近 N 天内订阅期刊的论文，按照 YAML 中的话题和关键词过滤，并生成报告。

## 配置文件说明

`config.yaml` 主要包括四部分：

### `app`

```yaml
app:
  opml_path: "feed-rss-list.opml"
  day_limit: 8
  output_dir: "outputs"
  output_language: "zh" # zh 或 en
```

- `opml_path`：默认订阅源文件路径。
- `day_limit`：默认抓取最近多少天。
- `output_dir`：报告输出目录。
- `output_language`：LLM 输出语言，`zh` 为简体中文，`en` 为英文。

### `llm`

```yaml
llm:
  endpoint_url: "https://example.com/v1/chat/completions"
  model_id: "your-model-id"
  auth_key: "your-auth-key"
  timeout: 120
```

该配置支持 OpenAI-compatible Chat Completions 接口。请替换为你自己的 endpoint、model 和鉴权 key。

### `topics`

```yaml
topics:
  Aging:
    positive_keywords:
      - aging
      - ageing
      - senescence
      - longevity
      - lifespan
    negative_keywords:
      - imaging
      - managing
      - packaging
      - staging
      - messaging
```

每个话题可以设置：

- `positive_keywords`：正向关键词，命中后该文章会进入该话题。
- `negative_keywords`：反向关键词，命中内容会在匹配前被 mask 掉。

例如，正向关键词包含 `aging` 时，`imaging` 和 `managing` 中的 `aging` 子串可能会造成误命中；将这两个词加入 `negative_keywords` 后，程序会先把它们替换为空格，再进行正向匹配。

### `email`

```yaml
email:
  enabled: true
  smtp_server: "smtp.qq.com"
  smtp_port: 465
  sender: "your-sender@example.com"
  password: "your-email-authorization-code"
  receivers:
    - "receiver@example.com"
  subject_prefix: "[RSS AI Report]"
```

- `enabled`：是否发送邮件，`false` 时不发送。
- `smtp_server` / `smtp_port`：SMTP 服务器和端口。
- `sender`：发件人邮箱。
- `password`：邮箱密码或授权码。
- `receivers`：收件人邮箱列表。

如果 Pandoc 可用，程序会把 HTML 版报告作为邮件正文发送；如果 Pandoc 不可用，则发送 Markdown 版报告。

## 命令行参数

```text
-c CONFIG, --config CONFIG
                       YAML 配置文件路径，默认 config.yaml
-d DAY_LIMIT, --day-limit DAY_LIMIT
                       覆盖配置文件中的时间范围
-f FILE_PATH, --file-path FILE_PATH
                       覆盖配置文件中的 OPML 路径
```

## 输出文件

每次运行会在 `output_dir` 下生成：

```text
RSS_primary_report_YYYYMMDD_HHMMSS.json
RSS_primary_report_YYYYMMDD_HHMMSS.md
RSS_primary_report_YYYYMMDD_HHMMSS_ai_report.parquet
RSS_primary_report_YYYYMMDD_HHMMSS_ai_report.xlsx
```

如果系统中检测到 Pandoc，还会额外生成：

```text
RSS_primary_report_YYYYMMDD_HHMMSS.html
```

各文件含义：

| 文件后缀 | 含义 |
| --- | --- |
| `.json` | 抓取并过滤后的论文元数据 |
| `.md` | Markdown 版论文阅读报告 |
| `.parquet` | AI 处理过程中保存的 DataFrame 中间文件 |
| `.xlsx` | 可二次编辑的 Excel 版报告 |
| `.html` | Pandoc 编译得到的 HTML 版报告，仅在 Pandoc 可用时生成 |

## 注意事项

- 当前 `config.yaml` 中已经写入了用户提供的测试 LLM 和邮箱账号，正式使用前请替换为自己的配置。
- 运行主程序会访问 RSS 源、期刊网站、PubMed/PMC、LLM 接口和 SMTP 服务器，请确保网络权限满足实际运行需求。
- 如果暂时不需要邮件发送，请将 `email.enabled` 改为 `false`。

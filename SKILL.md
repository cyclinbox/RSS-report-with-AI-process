---
name: rss-ai-report
description: Generate AI-assisted journal article reports from RSS/OPML subscriptions. Use when the user wants to fetch recent papers, filter them by topics and keywords, summarize them with an LLM, and optionally deliver Markdown or HTML reports by email.
---

# RSS AI Report

This skill runs the local `RSS-AI-report` tool, which turns RSS/OPML journal subscriptions into AI-generated literature reports.

## When to use

Use this skill when the user asks to:

- Fetch recent papers from an OPML or RSS subscription list.
- Filter papers by topics or keywords.
- Generate an AI summary or literature report.
- Configure the RSS report workflow or adjust its output language.
- Send a generated report by email.

Do not use this skill for unrelated data analysis, generic web scraping, or other document/report tasks.

## Key files

- `parse_opml-with-AI-process.py`: the main entrypoint.
- `config.yaml`: user configuration for feeds, topics, LLM, and email.
- `feed-rss-list.opml`: the default subscription list.
- `libqwen.py`: OpenAI-compatible LLM chat client.
- `libweb.py` and `libpubmed.py`: full-text retrieval helpers.

## Workflow

1. Run from this skill directory so relative paths and local imports resolve correctly.
2. Read `config.yaml` and confirm that `llm.endpoint_url`, `llm.model_id`, and `llm.auth_key` are filled in. If they are placeholders, tell the user to replace them before running.
3. Check `email.enabled`. If it is `true`, running the script sends real email to `email.receivers`; only run with email enabled when the user has asked for email delivery, otherwise set it to `false` or confirm first.
4. Run:

```bash
python parse_opml-with-AI-process.py -c config.yaml
```

Optional overrides:

```bash
python parse_opml-with-AI-process.py -c config.yaml -f other-feeds.opml -d 8
```

`-f` overrides the OPML file, and `-d` overrides the day limit.

5. Report where the outputs were saved. By default, results are written to `outputs/` as `.md`, `.json`, `.parquet`, and `.xlsx`; an `.html` file is also generated when Pandoc is available.

## Configuration notes

- `app.opml_path`: default OPML file.
- `app.day_limit`: how many recent days to fetch.
- `app.output_dir`: output directory.
- `app.output_language`: `zh` for simplified Chinese, `en` for English.
- `topics`: each topic has `positive_keywords` and `negative_keywords`. Negative keywords are masked out before positive matching, so words like `imaging` or `managing` are not matched as `aging`.
- `llm`: endpoint URL, model ID, auth key, and timeout for an OpenAI-compatible Chat Completions API.
- `email`: SMTP settings and receiver list. Pandoc is detected at runtime; if available, HTML is sent, otherwise Markdown is sent.

## Constraints

- Do not modify the Python source files during normal use; user changes belong in `config.yaml` or the OPML file.
- Do not print `auth_key`, email passwords, or authorization codes.
- Running the tool requires network access to RSS sources, journal pages, PubMed/PMC, the LLM endpoint, and, when enabled, the SMTP server.
- Preserve any existing files under `outputs/` unless the user asks to clean them.

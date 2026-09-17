#!python
#encoding=utf-8
import xml.etree.ElementTree as ET
import feedparser
import requests
from datetime import datetime, timedelta
import time
import re,difflib
from urllib.parse import urlparse
import os,sys,json
import argparse
import smtplib
import subprocess
import shutil
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr

import yaml
import libqwen,libweb,libpubmed
import pandas as pd

## Some config
DEFAULT_CONFIG_PATH = 'config.yaml'
DAY_LIMIT = 8
OPML_PATH = 'feed-rss-list.opml'
APP_CONFIG = {}
localtime = datetime.now().strftime("%Y%m%d_%H%M%S") # use this time as global timestamp for this run, to unify the output file names and report time.


def load_config(config_path):
    """Load configuration from an external YAML file."""
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}

    app_config = config.setdefault('app', {})
    llm_config = config.setdefault('llm', {})
    email_config = config.setdefault('email', {})

    if not isinstance(config.get('topics'), dict) or not config['topics']:
        raise ValueError("The YAML file must contain a non-empty `topics` mapping.")
    if not llm_config.get('endpoint_url'):
        raise ValueError("The YAML file must set `llm.endpoint_url`.")
    if not llm_config.get('model_id'):
        raise ValueError("The YAML file must set `llm.model_id`.")
    if not llm_config.get('auth_key'):
        raise ValueError("The YAML file must set `llm.auth_key`.")

    app_config.setdefault('opml_path', OPML_PATH)
    app_config.setdefault('day_limit', DAY_LIMIT)
    app_config.setdefault('output_dir', 'outputs')
    app_config.setdefault('output_language', 'zh')
    llm_config.setdefault('timeout', 120)
    email_config.setdefault('enabled', False)
    return config


def detect_pandoc():
    """Return the pandoc executable path, or None if it is not installed."""
    return shutil.which('pandoc')


def convert_markdown_to_html(md_path, output_dir, fileprefix):
    """Convert a Markdown report to standalone HTML using pandoc."""
    pandoc = detect_pandoc()
    if not pandoc:
        print("pandoc not found. Skipping HTML conversion.")
        return None

    html_path = os.path.join(output_dir, fileprefix + '.html')
    command = [
        pandoc,
        md_path,
        '-f', 'markdown',
        '-t', 'html',
        '-s',
        '--metadata', f'title={fileprefix}',
        '-o', html_path,
    ]
    subprocess.run(command, check=True)
    print(f"HTML file saved: {html_path}")
    return html_path


def send_email(subject, body, body_format, config):
    """Send the report by email according to the YAML email settings."""
    email_config = config.get('email', {})
    if not email_config.get('enabled', False):
        print("Email sending is disabled in config.yaml.")
        return False

    smtp_server = email_config.get('smtp_server')
    smtp_port = int(email_config.get('smtp_port', 465))
    sender = email_config.get('sender')
    password = email_config.get('password') or email_config.get('auth_code')
    receivers = email_config.get('receivers', [])
    if isinstance(receivers, str):
        receivers = [item.strip() for item in receivers.split(',') if item.strip()]

    if not all([smtp_server, sender, password, receivers]):
        print("Email configuration is incomplete. Skipping email sending.")
        return False

    if body_format == 'html':
        msg = MIMEText(body, 'html', 'utf-8')
    else:
        msg = MIMEText(body, 'plain', 'utf-8')

    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = formataddr((str(Header('RSS AI Report', 'utf-8')), sender))
    msg['To'] = ', '.join(receivers)

    try:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
            server.starttls()
        server.login(sender, password)
        server.sendmail(sender, receivers, msg.as_string())
        server.quit()
        print(f"Email sent to: {', '.join(receivers)}")
        return True
    except Exception as exc:
        print(f"Email sending failed: {exc}")
        return False

# Parse OPML file and get rss url.
def parse_opml(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    urls = []
    # find all `outline` element to extract xmlUrl attribution
    for outline in root.iter('outline'):
        xml_url = outline.get('xmlUrl')
        if xml_url:
            title = outline.get('title', outline.get('text', 'Unknown'))
            urls.append({'url': xml_url, 'title': title})
    return urls

# Scratch RSS source and get all terms.
def fetch_feed(url_info, days_limit=DAY_LIMIT):
    url = url_info['url']
    feed_title = url_info['title']
    print(f"Fetch: {feed_title} - {url}, \tday limit: {days_limit}")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
    except Exception as e:
        print(f"  Error: {e}")
        return []
    entries = []
    cutoff_date = datetime.now() - timedelta(days=days_limit)
    for entry in feed.entries: # get publish date.
        published_time = None
        if hasattr(entry, 'published_parsed'):
            published_time = datetime(*entry.published_parsed[:6])
        elif hasattr(entry, 'updated_parsed'):
            published_time = datetime(*entry.updated_parsed[:6])
        else:
            continue
        if published_time < cutoff_date:
            continue
        # Extract meta info
        title = entry.title if hasattr(entry, 'title') else 'Untitle'
        authors = []
        if hasattr(entry, 'authors'):
            authors = [author.name for author in entry.authors]
        elif hasattr(entry, 'author'):
            authors = [entry.author]
        # Abstract or summary
        summary = ''
        if hasattr(entry, 'summary'):
            summary = entry.summary
        elif hasattr(entry, 'description'):
            summary = entry.description
        # Article link
        link = entry.link if hasattr(entry, 'link') else ''
        entries.append({
            'title': title,
            'authors': authors,
            'journal': feed_title,
            'published': published_time.strftime("%Y-%m-%d"),
            'summary': summary,
            'link': link,
            'source_url': url
        })
    
    print(f" - {len(entries)} articles founded")
    return entries

# Filter articles by topics(by keyword matching)
def filter_by_topics(entries, topics_keywords):
    filtered = []
    for entry in entries:
        text_to_check = f"{entry['title']} {entry['summary']} {entry['journal']}".lower()
        for topic, topic_config in topics_keywords.items():
            if isinstance(topic_config, list):
                positive_keywords = topic_config
                negative_keywords = []
            else:
                positive_keywords = (
                    topic_config.get('positive_keywords')
                    or topic_config.get('keywords')
                    or []
                )
                negative_keywords = topic_config.get('negative_keywords') or []

            masked_text = text_to_check
            for keyword in negative_keywords:
                masked_text = re.sub(
                    re.escape(str(keyword).lower()),
                    ' ',
                    masked_text,
                    flags=re.IGNORECASE,
                )

            for keyword in positive_keywords:
                if str(keyword).lower() in masked_text:
                    entry['topic'] = topic
                    filtered.append(entry)
                    break
            if 'topic' in entry:
                break
    return filtered

# AI processing for single article.
def process_single_article(title, journal, published, abstract_text, article_text, output_language='zh'):
    language_name = 'simplified Chinese'
    if str(output_language).strip().lower() in ('en', 'english'):
        language_name = 'English'

    prompt = """
    ## Role
    You are an expert Population Genetics & Bioinformatics Literature Review Agent. 
    
    ## Task
    You specialize in analyzing complex evolutionary algorithms, advanced aging-related or longevity-related research, and cross-species data resource.
    
    ## Article Analysis Guidelines

    ### Information to Extract & Professional Logic:
    1. **Core Problem**: What's the key scientific question in this article?
    2. **Methodological Challenge**: What are the main technical challenges in solving this problem?
    3. **Innovative method**: Whether this article provided some new method/technology/algorithm? If yes, what is the core idea and how it works?
    4. **Data availability**: Whether the article provides data, and if so, what type of data and how to access it?
    5. **Key Conclusions**: What are the article's main findings?",

    ### Output Format Specification
    Generate a JSON object with the following exact structure:

    {
        "Title": "The translation of article title into (-language-)",
        "Type": "Research|Review|Method|Tool|Other",
        "Abstract_translation": "The translation of the abstract into (-language-)",
        "Core_Problem": "A statement of the main scientific questions, keep all in one line",
        "Meth_Chllg": "A statement of the main technical challenges, keep all in one line",
        "Innovative_Method": "A statement of article's innovative methods, keep all in one line",
        "Is_data": "Yes|No",
        "data": "Dataset URLs or identifiers if Is_data='Yes'; otherwise '-'",
        "Is_code": "Yes|No",
        "code": "Code repository/tool links if Is_code='Yes'; otherwise '-'",
        "Key_Conclusions": "A detailed summary of main findings, keep all in one line"
    }

    ### Notes:
    - Use "Yes"/"No" consistently for boolean fields.
    - For missing information, use "-" as placeholder.
    - All statements should be concise, factual statements, and **keep all statements/summary in one line**.

    ## Article Content

    ### Title & Abstract:

    ```
    (-abstract-)
    ```

    ### Full Text:

    ```
    (-content-)
    ```

    ## Output Instruction

    Generate the complete JSON object based on your analysis.
    Now produce the JSON.
    """
    llm_config = APP_CONFIG.get('llm', {})
    res = libqwen.chat(
        prompt.replace("(-abstract-)", abstract_text).replace("(-content-)", article_text).replace("(-language-)", language_name),
        endpoint_url=llm_config['endpoint_url'],
        model_id=llm_config['model_id'],
        auth_key=llm_config['auth_key'],
        timeout=llm_config.get('timeout', 120),
    )
    res1 = res.replace("```json", "").replace("```", "").strip()
    json_match = re.search(r'\{.*\}', res1, flags=re.S)
    if json_match:
        res1 = json_match.group(0)
    res_df = pd.DataFrame()     
    error_msg_dt = {}     
    try:
        dt1 = json.loads(res1)
        df1 = pd.DataFrame(dt1,index=[0])
        df1["journal"] = journal
        df1["published"] = published
        res_df = pd.concat([res_df,df1],axis = 0)
        res_df = res_df.reset_index(drop=True)
    except Exception as e:
        print(f"An error or exception occurred while processing article: {title}. Message: {e}")
        error_msg_dt[title] = e
        pass
    return res_df


## Calculate the credibility score of article text
def calculate_credibility_score(text, original_title, abstract_length):
    # Grading Criteria
    #1. Basic score: +1  if not-empty
    #2. Integrity:   +5  if integrity(text length 5-fold longer than summary text)
    #3. Precision:   +10 if Origin title occurred in the text(Which means we find the correct text)
    if not text or not text.strip():
        return 0
    score = 1.0 
    # 1. Check integrity by text length
    if len(text) > abstract_length * 5:
        score += 5.0
    # 2. Check Precision by title string matching
    # If libpubmed queried similar(but not we want) article, the article title may not match
    if original_title.strip().lower() in text.lower():
        score += 10.0
    else:
        text_preview = text[:500].lower()
        title_ratio = difflib.SequenceMatcher(None, original_title.lower(), text_preview).ratio()
        if title_ratio > 0.8: 
            score += 10.0
    return score


## Process article for each term
def process_articles(entry):
    title = entry['title']
    journal = entry['journal']
    published = entry['published']
    abstract_text = "**Title**:"+ title + ". **Summary**: " + journal + ". " + re.sub(r'<[^>]+>', '', entry['summary']).replace("\r","").replace("\n","")
    article_text_from_web = libweb.run(entry['link'])
    article_text_from_pmc = libpubmed.get_and_return_single_text_by_title(title)
    score_web = calculate_credibility_score(article_text_from_web, title, len(abstract_text))
    score_pmc = calculate_credibility_score(article_text_from_pmc, title, len(abstract_text))
    print(f"Source score: [web]{score_web}\t[pmc]{score_pmc}.")
    if(score_pmc>score_web): article_text = article_text_from_pmc
    else:                    article_text = article_text_from_web
    return process_single_article(
        title,
        journal,
        published,
        abstract_text,
        article_text,
        output_language=APP_CONFIG.get('app', {}).get('output_language', 'zh'),
    )

def main():
    # Argument process
    global DAY_LIMIT, OPML_PATH, APP_CONFIG, localtime
    parser = argparse.ArgumentParser(
        description="AI based RSS article processing tool.",
        formatter_class=argparse.RawDescriptionHelpFormatter 
    )
    parser.add_argument(
        '-c', '--config', type=str, default=DEFAULT_CONFIG_PATH,
        help=f'YAML config path. Default: "{DEFAULT_CONFIG_PATH}".'
    )
    parser.add_argument(
        '-d', '--day-limit', type=int, default=None,
        help='Day limit. If omitted, use the value in config.yaml.'
    )
    parser.add_argument(
        '-f', '--file-path', type=str, default=None,
        help='OPML file path. If omitted, use the value in config.yaml.'
    )
    args = parser.parse_args()
    APP_CONFIG = load_config(args.config)
    app_config = APP_CONFIG.get('app', {})

    DAY_LIMIT = args.day_limit if args.day_limit is not None else int(app_config.get('day_limit', DAY_LIMIT))
    OPML_PATH = args.file_path or app_config.get('opml_path', OPML_PATH)
    output_dir = app_config.get('output_dir', 'outputs')
    localtime = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(output_dir, exist_ok=True)

    ## Main process
    topics_keywords = APP_CONFIG['topics']
    print("Parse OPML file...")
    urls = parse_opml(OPML_PATH)
    print(f"Founded {len(urls)} RSS contents")
    all_entries = []
    for url_info in urls:
        entries = fetch_feed(url_info, days_limit=DAY_LIMIT)
        all_entries.extend(entries)
        time.sleep(0.2)
    
    print(f"Totally {len(all_entries)} recent articles")
    filtered_entries = filter_by_topics(all_entries, topics_keywords)
    print(f"After filter, remain {len(filtered_entries)} articles")
    
    # Categorized by topic
    categorized = {topic: [] for topic in topics_keywords.keys()}
    for entry in filtered_entries:
        categorized[entry['topic']].append(entry)
    
    # Sort by published time(new to old)
    for topic in categorized:
        categorized[topic].sort(key=lambda x: x['published'], reverse=True)
    cutoff_date = datetime.now() - timedelta(days=DAY_LIMIT)
    
    # Generating Markdown report with AI report
    print("======== Generating Markdown report with AI analysis ========")
    report = "# Biomedical article tracing report(primary report)\n\n"
    report += f"> Report time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n>\n"
    report += f"> Article range: Published in {DAY_LIMIT} days (from {cutoff_date.strftime('%Y-%m-%d')} to {datetime.now().strftime('%Y-%m-%d')})\n>\n"
    report += f"> Total number: {len(filtered_entries)}\n\n------\n\n"
    
    if len(filtered_entries) == 0:
        report += "## Warning\n\n"
        report += "Zero article was founded. Possible reasons:\n"
        report += "1. Now too many articles were published on candidate journal. \n"
        report += "2. Technical error during RSS processing. \n"
        report += "3. Key word matching failed. \n"
    else:
        merged_ai_report_df = pd.DataFrame()
        for topic, entries in categorized.items():
            if entries:
                report += f"\n\n------\n\n## {topic}\n\n"
                for i, entry in enumerate(entries, 1):
                    print(f"[{i}] {entry['title']}")
                    report += f"#### 🔵 {i}. {entry['title']}\n\n"
                    report += f"- 👨‍🎓: {', '.join(entry['authors']) if entry['authors'] else '-'}\n"
                    report += f"- 📰: **{entry['journal']}**\n"
                    report += f"- ⏰: **{entry['published']}**\n"
                    summary = re.sub(r'<[^>]+>', '', entry['summary'])
                    summary = summary.replace("\r",". ").replace("\n",". ")
                    if len(summary) > 500:
                        report += f"- 📌: *{summary[:500]}...*\n"
                    else:
                        report += f"- 📌: *{summary}*\n"
                    report += f"- 🔗: [{entry['link']}]({entry['link']})\n\n"
                    ## Run AI report.
                    ai_report_df = process_articles(entry)
                    if not ai_report_df.empty:
                        merged_ai_report_df = pd.concat([merged_ai_report_df, ai_report_df], axis=0)
                        merged_ai_report_df.reset_index(drop=True, inplace=True)
                        merged_ai_report_df.to_parquet(os.path.join(output_dir, f"RSS_primary_report_{localtime}_ai_report.parquet"), compression="brotli")
                        ai_report = ai_report_df.iloc[0]
                        report += f"- **AI Analysis**\n\n"
                        report += f"| **Field** | **Content** |\n"
                        report += f"|:----------|:------------|\n"
                        report += f"| **Title**| {ai_report['Title']}|\n"
                        report += f"| **Type** | {ai_report['Type']}|\n"
                        report += f"| **Abstract** | {ai_report['Abstract_translation']}|\n"
                        report += f"| **Core Problem**| {ai_report['Core_Problem']}|\n"
                        report += f"| **Challenge**| {ai_report['Meth_Chllg']}|\n"
                        report += f"| **Method**| {ai_report['Innovative_Method']}|\n"
                        report += f"| **Data**| {ai_report['Is_data']} ({ai_report['data']})|\n"
                        report += f"| **Code**| {ai_report['Is_code']} ({ai_report['code']})|\n"
                        report += f"| **Conclusions**  | {ai_report['Key_Conclusions']}|\n\n"
                    else:
                        report += "- AI analysis failed for this article.\n\n"
        ## Export a copy in Excel file
        #  You can do more options in Excel
        ew = pd.ExcelWriter(os.path.join(output_dir, f"RSS_primary_report_{localtime}_ai_report.xlsx"))
        merged_ai_report_df.to_excel(ew,sheet_name="all data")
        ew.close()
    # Save markdown report
    os.makedirs(output_dir, exist_ok=True)
    fileprefix = f"RSS_primary_report_{localtime}"
    output_file = os.path.join(output_dir, fileprefix+".md")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"File saved: {output_file}")
    # Save json file
    json_text = json.dumps(categorized,ensure_ascii=False,indent="\t")
    output_file = os.path.join(output_dir, fileprefix+".json")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(json_text)
    print(f"File saved: {output_file}")

    # Optional HTML conversion with pandoc.
    html_path = convert_markdown_to_html(output_file, output_dir, fileprefix)

    # Optional email delivery. HTML is preferred when pandoc is available;
    # otherwise the Markdown version is sent.
    if APP_CONFIG.get('email', {}).get('enabled', False):
        email_config = APP_CONFIG.get('email', {})
        subject_prefix = email_config.get('subject_prefix', '[RSS AI Report]')
        subject = f"{subject_prefix} {localtime}"
        if html_path:
            with open(html_path, 'r', encoding='utf-8') as f:
                email_body = f.read()
            send_email(subject, email_body, 'html', APP_CONFIG)
        else:
            send_email(subject, report, 'plain', APP_CONFIG)

    return categorized

if __name__ == '__main__':
    main()

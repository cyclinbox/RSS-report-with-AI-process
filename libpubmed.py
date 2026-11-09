#!/usr/bin/env python
# coding: utf-8
# E-utilities edition of the original pubmed_spider_qwen_edition.py
# Migrated 2026-11-09

"""
PubMed/PMC spider backed by NCBI E-utilities.

The public command-line arguments and the four primary helper contracts remain
compatible with the original script:

    get_pmid_by_title(title) -> list[str]
    get_pmcid_by_pmid(pmid) -> list[str]
    get_abstract_by_pmid(pmid, plain_text=True) -> str | list[str]
    get_fulltext_by_pmcid(pmcid) -> str

Set NCBI_API_KEY in the environment before using the script. NCBI_EMAIL and
NCBI_TOOL are optional but recommended by NCBI. If the module is imported by
another program, the corresponding module globals may also be assigned before
the first request.
"""

import argparse
import datetime
import json
import os
import re
import sys
import threading
import time
from xml.etree import ElementTree as ET

import pandas as pd
import requests
from bs4 import BeautifulSoup


# ==========================================
# E-utilities configuration
# ==========================================

NCBI_API_KEY = os.getenv("NCBI_API_KEY", "ae4e7d262dc452dece0be6c4a7e06d9ccc09")
NCBI_EMAIL = os.getenv("NCBI_EMAIL", "")
NCBI_TOOL = os.getenv("NCBI_TOOL", "pubmed_spider_qwen_eutils")

EUTILS_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
REQUEST_TIMEOUT = 45
MAX_RETRIES = 3

_RATE_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "Accept": "application/json, application/xml, text/xml",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120 Safari/537.36 "
            "pubmed-spider-eutils/1.0"
        ),
    }
)


def _redact_secret(message):
    """Prevent an API key from leaking through a requests exception."""
    text = str(message)
    if NCBI_API_KEY:
        text = text.replace(NCBI_API_KEY, "[REDACTED]")
    return text


def _throttle_requests():
    """Respect NCBI's 3/s unauthenticated and 10/s authenticated limits."""
    global _LAST_REQUEST_AT
    min_interval = 0.11 if NCBI_API_KEY else 0.35
    with _RATE_LOCK:
        elapsed = time.monotonic() - _LAST_REQUEST_AT
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _LAST_REQUEST_AT = time.monotonic()


def _request_eutils(endpoint, params):
    """
    Make one resilient E-utilities request.

    Returns a requests.Response on success and None on an exhausted error.
    API keys are never printed.
    """
    query = {
        "tool": NCBI_TOOL,
        "api_key": NCBI_API_KEY,
    }
    if NCBI_EMAIL:
        query["email"] = NCBI_EMAIL
    query.update(params)

    url = EUTILS_BASE_URL + endpoint
    last_error = None
    for attempt in range(MAX_RETRIES):
        _throttle_requests()
        try:
            response = _SESSION.get(url, params=query, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            last_error = _redact_secret(exc)
            if attempt + 1 < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            print(f"E-utilities request failed ({endpoint}): {last_error}")
            return None

        if response.status_code == 429 or response.status_code >= 500:
            last_error = f"HTTP {response.status_code}"
            if attempt + 1 < MAX_RETRIES:
                retry_after = response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else 2 ** attempt
                time.sleep(max(delay, 1))
                continue
            print(f"E-utilities request failed ({endpoint}): {last_error}")
            return None

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            print(
                f"E-utilities request failed ({endpoint}): "
                f"{_redact_secret(exc)}"
            )
            return None
        return response

    print(f"E-utilities request failed ({endpoint}): {last_error}")
    return None


def _request_eutils_json(endpoint, params):
    response = _request_eutils(endpoint, params)
    if response is None:
        return None
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Invalid JSON from E-utilities ({endpoint}): {exc}")
        return None


def _request_eutils_xml(endpoint, params):
    response = _request_eutils(endpoint, params)
    if response is None:
        return None
    try:
        return ET.fromstring(response.content)
    except ET.ParseError as exc:
        print(f"Invalid XML from E-utilities ({endpoint}): {exc}")
        return None


def _node_text(node):
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def _clean_title_query(title):
    text = str(title).replace("“", " ").replace("”", " ").replace('"', " ")
    return re.sub(r"\s+", " ", text).strip()


def _clean_pmcid(pmcid):
    match = re.search(r"(\d+)", str(pmcid))
    return f"PMC{match.group(1)}" if match else ""


# ==========================================
# Primary retrieval functions
# ==========================================

def get_pmid_by_title(title):
    # param: string, article title
    # return: list, a list of PubMed PMID strings
    cleaned_title = _clean_title_query(title)
    if not cleaned_title:
        return []

    # A title-field phrase search most closely matches the old PubMed search.
    searches = [f'"{cleaned_title}"[Title]', f"{cleaned_title}[Title]"]
    for search_term in searches:
        payload = _request_eutils_json(
            "esearch.fcgi",
            {
                "db": "pubmed",
                "term": search_term,
                "retmode": "json",
                "retmax": 100,
            },
        )
        if payload is None:
            continue

        result = payload.get("esearchresult", {})
        pmid_list = []
        for pmid in result.get("idlist", []):
            pmid = str(pmid).strip()
            if pmid and pmid not in pmid_list:
                pmid_list.append(pmid)
        if pmid_list:
            return pmid_list

    return []


def get_pmcid_by_pmid(pmid):
    # param: string/int, PubMed PMID
    # return: list, PMCID strings such as ["PMC1234567"]
    pmid = str(pmid).strip()
    if not pmid:
        return []

    payload = _request_eutils_json(
        "elink.fcgi",
        {
            "dbfrom": "pubmed",
            "db": "pmc",
            "id": pmid,
            "retmode": "json",
        },
    )
    if payload is None:
        return []

    pmcid_list = []
    for linkset in payload.get("linksets", []):
        for linksetdb in linkset.get("linksetdbs", []):
            # pubmed_pmc is the direct article link. Other link sets in the same
            # response are usually reference/citation relationships.
            if linksetdb.get("linkname") != "pubmed_pmc":
                continue
            for linked_id in linksetdb.get("links", []):
                pmcid = _clean_pmcid(linked_id)
                if pmcid and pmcid not in pmcid_list:
                    pmcid_list.append(pmcid)
    return pmcid_list


def _article_authors(article):
    authors = []
    for author in article.findall("./AuthorList/Author"):
        collective = author.findtext("CollectiveName")
        if collective:
            authors.append(_node_text(collective))
            continue
        last_name = author.findtext("LastName") or ""
        fore_name = author.findtext("ForeName") or author.findtext("Initials") or ""
        name = " ".join(part for part in (fore_name.strip(), last_name.strip()) if part)
        if name:
            authors.append(name)
    return authors


def _article_publication_header(pmid, article):
    title = _node_text(article.find("./ArticleTitle"))
    authors = _article_authors(article)
    journal = _node_text(article.find("./Journal/Title"))
    journal_issue = article.find("./Journal/JournalIssue")
    volume = _node_text(journal_issue.find("./Volume")) if journal_issue is not None else ""
    issue = _node_text(journal_issue.find("./Issue")) if journal_issue is not None else ""

    year = ""
    if journal_issue is not None:
        pub_date = journal_issue.find("./PubDate")
        if pub_date is not None:
            year = (
                pub_date.findtext("Year")
                or pub_date.findtext("MedlineDate")
                or ""
            ).strip()

    pagination = article.find("./Pagination")
    pages = ""
    if pagination is not None:
        pages = (
            pagination.findtext("MedlinePgn")
            or pagination.findtext("StartPage")
            or ""
        ).strip()

    citation = year
    if volume:
        citation += f";{volume}"
    if issue:
        citation += f"({issue})"
    if pages:
        citation += f":{pages}"

    doi = ""
    for article_id in article.findall("./ELocationID"):
        if article_id.attrib.get("EIdType") == "doi":
            doi = _node_text(article_id)
            break

    header_parts = []
    if title:
        header_parts.append(f"Title: {title}")
    if authors:
        header_parts.append(f"Authors: {', '.join(authors)}")
    if journal:
        header_parts.append(f"Journal: {journal}")
    if citation.strip(";"):
        header_parts.append(f"Citation: {citation}")
    header_parts.append(f"PMID: {pmid}")
    if doi:
        header_parts.append(f"DOI: {doi}")
    return "\n".join(header_parts)


def _article_abstract(article):
    abstract_nodes = article.findall("./Abstract/AbstractText")
    abstract_parts = []
    for node in abstract_nodes:
        text = _node_text(node)
        if not text:
            continue
        label = (node.attrib.get("Label") or "").strip()
        if label and not text.lower().startswith(label.lower()):
            text = f"{label}: {text}"
        abstract_parts.append(text)
    return "\n".join(abstract_parts)


def get_abstract_by_pmid(pmid, plain_text=True):
    # param: string/int, PubMed PMID
    # return:
    #   plain_text=True  -> "header\n## Abstract\nabstract" (str)
    #   plain_text=False -> [header, abstract] (list[str])
    pmid = str(pmid).strip()
    if not pmid:
        return ""

    root = _request_eutils_xml(
        "efetch.fcgi",
        {
            "db": "pubmed",
            "id": pmid,
            "retmode": "xml",
            "rettype": "abstract",
        },
    )
    if root is None:
        return ""

    article = root.find(".//PubmedArticle/MedlineCitation/Article")
    if article is None:
        print(f"No PubMed article metadata found for PMID '{pmid}'.")
        return ""

    header_text = _article_publication_header(pmid, article)
    abstract_text = _article_abstract(article)
    if plain_text:
        return header_text + "\n## Abstract\n" + abstract_text
    return [header_text, abstract_text]


_BLOCK_XML_TAGS = {
    "abstract",
    "article-title",
    "caption",
    "disp-formula",
    "fig",
    "list-item",
    "p",
    "sec",
    "table",
    "table-wrap",
    "td",
    "th",
    "title",
    "tr",
}


def _element_to_plain_text(element):
    """Convert JATS XML into readable plain text with block boundaries."""
    if element is None:
        return ""
    pieces = []

    def walk(node):
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in _BLOCK_XML_TAGS:
            pieces.append("\n")
        if node.text:
            pieces.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                pieces.append(child.tail)
        if tag in _BLOCK_XML_TAGS:
            pieces.append("\n")

    walk(element)
    lines = []
    for line in "".join(pieces).splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def get_fulltext_by_pmcid(pmcid):
    # param: string, PMCID such as "PMC8558904"
    # return: string, readable full text; empty when unavailable
    normalized_pmcid = _clean_pmcid(pmcid)
    if not normalized_pmcid:
        return ""

    root = _request_eutils_xml(
        "efetch.fcgi",
        {
            "db": "pmc",
            "id": normalized_pmcid,
            "retmode": "xml",
        },
    )
    if root is None:
        return ""

    article = root.find(".//article")
    if article is None:
        print(f"No PMC article found for '{normalized_pmcid}'.")
        return ""

    title = _node_text(article.find("./front/article-meta/title-group/article-title"))
    abstract = _element_to_plain_text(article.find("./front/article-meta/abstract"))
    body = _element_to_plain_text(article.find("./body"))

    sections = []
    if title:
        sections.append(f"# {title}")
    if abstract:
        sections.append("## Abstract\n" + abstract)
    if body:
        sections.append(body)
    return "\n\n".join(sections).strip()


# ==========================================
# Legacy HTML helper placeholders
# ==========================================

def get_bs4obj_by_pmid(pmid):
    """
    PLACEHOLDER: not implementable in an E-utilities-only backend.

    The old function returned a BeautifulSoup DOM parsed from the PubMed web
    page. E-utilities returns JSON/XML instead, so there is no equivalent HTML
    DOM object to return. Use get_abstract_by_pmid(pmid).
    """
    return None


def get_pmcid_by_bs4obj(bs4obj):
    """
    PLACEHOLDER: not implementable in an E-utilities-only backend.

    PMCIDs now come from ELink. Use get_pmcid_by_pmid(pmid).
    """
    return []


def get_abstract_by_bs4obj(bs4obj, plain_text=True):
    """
    PLACEHOLDER: not implementable in an E-utilities-only backend.

    The old function read PubMed HTML. Use get_abstract_by_pmid(pmid,
    plain_text=plain_text).
    """
    return ""


# ==========================================
# File saving and cleanup helpers
# ==========================================

def sanitize_filename(filename):
    invalid_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(invalid_chars, "_", str(filename)).strip()
    return sanitized or "untitled"


def save_content(content, filename, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    try:
        with open(filepath, "w", encoding="utf-8") as file_handle:
            file_handle.write(content)
        print(f"Saved: {filepath}")
    except Exception as exc:
        print(f"Error saving file {filepath}: {exc}")


def download_and_save_by_pmid(pmid, output_dir):
    """
    Fetch abstract/full text by PMID and save the best available text.
    """
    print(f"Processing PMID: {pmid}")
    abstract_text = get_abstract_by_pmid(pmid)
    pmcid_list = get_pmcid_by_pmid(pmid)
    full_text = ""

    if pmcid_list:
        full_text = get_fulltext_by_pmcid(pmcid_list[0])

    content = full_text if full_text else abstract_text
    if not content:
        print(f"No abstract or full text available for PMID: {pmid}")
        return
    filename = f"autosave-{pmid}.txt"
    save_content(content, filename, output_dir)


def download_and_save_by_title(title, output_dir):
    """
    Search PMIDs by title, then fetch and save each match.
    """
    print(f"Searching title: {title}")
    pmid_list = get_pmid_by_title(title)
    if not pmid_list:
        print(f"No PMID found for title: {title}")
        return

    for pmid in pmid_list:
        download_and_save_by_pmid(pmid, output_dir)
        time.sleep(1)


def download_and_save_by_url(url, output_dir):
    """
    Scrape a non-NCBI generic URL and save its visible text.
    """
    print(f"Processing URL: {url}")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) "
            "Gecko/20100101 Firefox/115.0"
        )
    }
    try:
        request = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        request.raise_for_status()
        request.encoding = request.apparent_encoding or "utf-8"
        bs4obj = BeautifulSoup(request.text, "lxml")

        page_title = ""
        if bs4obj.title and bs4obj.title.string:
            page_title = bs4obj.title.string
        if not page_title:
            page_title = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        main_text = bs4obj.get_text().strip()
        safe_title = sanitize_filename(page_title)
        filename = f"{safe_title}.txt"
        if len(filename) > 200:
            timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            filename = f"{safe_title[:100]}_{timestamp}.txt"
        save_content(main_text, filename, output_dir)
    except Exception as exc:
        print(f"Error processing URL {url}: {_redact_secret(exc)}")


# ==========================================
# Input mode handling
# ==========================================

def process_csv(path, output_dir):
    print(f"Mode: CSV Input -> {path}")
    try:
        article_df = pd.read_csv(path, encoding="utf-8-sig")
        if "Title" not in article_df.columns:
            print("Error: CSV file must contain a 'Title' column.")
            return
        title_list = list(article_df["Title"])
        for index, title in enumerate(title_list):
            if pd.isna(title) or not str(title).strip():
                print(f"[{index + 1}/{len(title_list)}] Skipping empty title")
                continue
            print(f"[{index + 1}/{len(title_list)}] Processing article: `{title}`")
            download_and_save_by_title(str(title), output_dir)
            time.sleep(1)
    except Exception as exc:
        print(f"Error processing CSV: {exc}")


def process_pmid_list(path, output_dir):
    print(f"Mode: PMID List Input -> {path}")
    try:
        with open(path, "r", encoding="utf-8") as file_handle:
            lines = file_handle.readlines()

        pmids = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pmids.append(line)

        for index, pmid in enumerate(pmids):
            print(f"[{index + 1}/{len(pmids)}] Processing PMID: `{pmid}`")
            download_and_save_by_pmid(pmid, output_dir)
            time.sleep(1)
    except Exception as exc:
        print(f"Error processing PMID list: {exc}")


def is_pmid_line(line):
    return re.match(r"^\d+$", line.strip()) is not None


def process_auto_file(path, output_dir):
    print(f"Mode: Auto Detect File -> {path}")
    _, ext = os.path.splitext(path)

    if ext.lower() == ".csv":
        try:
            dataframe = pd.read_csv(path, nrows=5, encoding="utf-8-sig")
            if "Title" in dataframe.columns:
                print("Detected: Zotero CSV (Title column found)")
                process_csv(path, output_dir)
                return
        except Exception:
            pass

    try:
        with open(path, "r", encoding="utf-8-sig") as file_handle:
            lines = file_handle.readlines()

        valid_lines = [
            line.strip()
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        ]
        if not valid_lines:
            print("File is empty or invalid.")
            return

        pmid_count = sum(1 for line in valid_lines if is_pmid_line(line))
        ratio = pmid_count / len(valid_lines)

        if ratio > 0.8:
            print("Detected: PMID List (Majority numeric)")
            process_pmid_list(path, output_dir)
        else:
            print("Detected: Title List (Fallback)")
            for index, line in enumerate(valid_lines):
                print(f"[{index + 1}/{len(valid_lines)}] Processing title: `{line}`")
                download_and_save_by_title(line, output_dir)
                time.sleep(1)
    except Exception as exc:
        print(f"Error auto detecting file: {exc}")


def process_single_pmid(pmid, output_dir):
    print(f"Mode: Single PMID -> {pmid}")
    download_and_save_by_pmid(pmid, output_dir)


def process_single_title(title, output_dir):
    print(f"Mode: Single Title -> {title}")
    download_and_save_by_title(title, output_dir)


def process_single_url(url, output_dir):
    print(f"Mode: Single URL -> {url}")
    download_and_save_by_url(url, output_dir)

def get_and_return_single_text_by_title(title):
    # param: string, article title
    # return: list, a list of pmids
    try:
        pmid_list = get_pmid_by_title(title)
        pmcid_list = get_pmcid_by_pmid(pmid_list[0])
        main_text = get_fulltext_by_pmcid(pmcid_list[0])
    except Exception as e:
        print(f"Warning: in `get_and_return_single_text_by_title`, an Exception occurred with message:{e}.")
        main_text = ""
    return main_text

# ==========================================
# Main entry point
# ==========================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PubMed/PMC Spider with Multiple Input Modes"
    )

    group = parser.add_argument_group("Input Modes")
    group.add_argument(
        "--csv",
        "-c",
        type=str,
        help='Path to Zotero exported CSV file (requires "Title" column)',
    )
    group.add_argument(
        "--list",
        "-l",
        type=str,
        help="Path to text file containing PMIDs (one per line, # for comments)",
    )
    group.add_argument(
        "--file",
        "-f",
        type=str,
        help="Path to file. Auto-detect format (CSV, PMID list, or Title list)",
    )
    group.add_argument("--pmid", "-i", type=str, help="Single PMID to download")
    group.add_argument(
        "--title", "-t", type=str, help="Single Article Title to search and download"
    )
    group.add_argument(
        "--url", "-u", type=str, help="Single URL to scrape content directly"
    )

    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="saves",
        help="Output directory path (default: saves)",
    )

    args = parser.parse_args()
    output_dir = args.output

    if args.pmid:
        process_single_pmid(args.pmid, output_dir)
    elif args.title:
        process_single_title(args.title, output_dir)
    elif args.url:
        process_single_url(args.url, output_dir)
    elif args.csv:
        process_csv(args.csv, output_dir)
    elif args.list:
        process_pmid_list(args.list, output_dir)
    elif args.file:
        process_auto_file(args.file, output_dir)
    else:
        parser.print_help()
        print(
            "\nError: No input mode specified. Please use one of: "
            "--csv, --list, --file, --pmid, --title, --url"
        )
        sys.exit(1)

    print("\nAll tasks completed.")

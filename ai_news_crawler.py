#!/usr/bin/env python3
"""AI News Aggregator - professional, configurable crawler

Features:
- sources.json driven
- per-source parsers (list pages & RSS)
- 24-hour rolling window filtering (configurable)
- translation support (deep-translator)
- resilient: per-source try/except, continue on failure
- progress bar (tqdm) and colored console output
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import re
import sys
import time
from typing import Dict, List, Optional, Tuple
import traceback
import urllib3

import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator, MyMemoryTranslator
from email.utils import parsedate_to_datetime
from tqdm import tqdm
import colorama
from colorama import Fore, Style

# disable insecure request warnings when verify=False is used (local testing)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# initialize colorama for Windows compatibility
colorama.init(autoreset=True)
COLOR_RESET = Style.RESET_ALL
COLOR_RED = Fore.RED
COLOR_GREEN = Fore.GREEN
COLOR_YELLOW = Fore.YELLOW
COLOR_CYAN = Fore.CYAN

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(BASE_DIR, "sources.json")
DEFAULT_TIMEOUT = 15
REQUEST_DELAY_RANGE = (1.5, 3.5)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/605.1.15",
]

SUPPORTED_LANGS = {"zh-CN": "简体中文", "zh-TW": "繁體中文", "en": "English", "ja": "日本語"}


def eprint(*args, **kwargs):
    print(*args, **kwargs, file=sys.stderr)


def color_text(text: str, color: str) -> str:
    return f"{color}{text}{COLOR_RESET}"


def load_sources(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"sources.json not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_session(timeout: int = DEFAULT_TIMEOUT) -> requests.Session:
    s = requests.Session()
    s.timeout = timeout
    s.trust_env = True  # allow HTTP_PROXY / HTTPS_PROXY from env
    # default to not verify SSL so local testing can bypass cert issues; individual calls also pass verify=False
    s.verify = False
    s.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": random.choice(USER_AGENTS),
    })
    return s


def get_random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def random_delay():
    delay = random.uniform(*REQUEST_DELAY_RANGE)
    time.sleep(delay)


def render_with_selenium(url: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[str]:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        eprint(color_text("Warning: Selenium or webdriver-manager is not installed; SPA rendering skipped.", COLOR_YELLOW))
        return None

    try:
        options = Options()
        options.headless = True
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(f"--user-agent={get_random_user_agent()}")
        options.add_argument("--window-size=1920,1080")
        service = Service(ChromeDriverManager().install())
        with webdriver.Chrome(service=service, options=options) as driver:
            driver.set_page_load_timeout(timeout)
            driver.get(url)
            time.sleep(2)
            return driver.page_source
    except Exception as e:
        eprint(color_text(f"Warning: Selenium rendering failed for {url}: {e}", COLOR_YELLOW))
        return None


def fetch_page_content(url: str, session: requests.Session, use_selenium: bool = False) -> Optional[str]:
    if use_selenium:
        rendered = render_with_selenium(url, DEFAULT_TIMEOUT)
        if rendered:
            return rendered

    try:
        session.headers["User-Agent"] = get_random_user_agent()
        resp = session.get(url, timeout=DEFAULT_TIMEOUT, verify=False)
        resp.raise_for_status()
        return resp.content.decode("utf-8", errors="replace")
    except Exception as e:
        eprint(color_text(f"Warning: request failed for {url}: {e}", COLOR_YELLOW))
        return None


def parse_feed(xml_bytes: bytes) -> List[Dict[str, str]]:
    # lightweight RSS/Atom parser using simple regex/ElementTree could be used; here we try email.utils for date parse
    items: List[Dict[str, str]] = []
    try:
        text = xml_bytes.decode("utf-8", errors="replace")
        soup = BeautifulSoup(text, "xml")
        for entry in soup.find_all(["item", "entry"]):
            title = entry.find("title")
            link = entry.find("link")
            description = entry.find(["description", "summary", "content"]) or entry.find("encoded")
            pub = entry.find(["pubDate", "published", "updated"]) or entry.find("date")
            link_url = ""
            if link:
                # <link href="..."/> or <link>...</link>
                link_url = link.get("href") or (link.string or "")
            items.append(
                {
                    "title": title.string.strip() if title and title.string else "",
                    "link": link_url.strip(),
                    "summary": description.string.strip() if description and description.string else "",
                    "published": pub.string.strip() if pub and pub.string else "",
                }
            )
    except Exception:
        # fallback: no items
        pass
    return items


def extract_article_body_and_image(html: str, base_url: str) -> Tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    # first: try <article>
    candidates = []
    if soup.find("article"):
        candidates.append(soup.find("article"))
    if soup.find("main"):
        candidates.append(soup.find("main"))
    selectors = [
        "div[class*=article]",
        "div[class*=content]",
        "div[class*=post]",
        "div[class*=story]",
        "section[class*=article]",
        "section[class*=content]",
    ]
    for sel in selectors:
        candidates.extend(soup.select(sel))

    def clean_text(el):
        return " ".join(p.get_text(separator=" ", strip=True) for p in el.find_all(["p", "h1", "h2", "h3", "li"]) if p.get_text(strip=True))

    for cand in candidates:
        text = clean_text(cand)
        if len(text.split()) >= 40:
            # find first image inside candidate
            img = cand.find("img")
            img_url = img.get("src") if img and img.get("src") else ""
            return text.strip(), (requests.compat.urljoin(base_url, img_url) if img_url else "")

    # fallback to meta description
    meta = soup.find("meta", attrs={"property": "og:description"}) or soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return meta.get("content").strip(), ""

    # fallback to first big paragraph
    p = soup.find("p")
    if p:
        return p.get_text(separator=" ", strip=True), ""

    return "", ""


def parse_published_date(date_str: str) -> Optional[datetime.datetime]:
    if not date_str:
        return None
    date_str = date_str.strip()
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        # try simple yyyy-mm-dd or yyyy年mm月dd日
        m = re.search(r"(\d{4})[\-年/](\d{1,2})[\-月/](\d{1,2})", date_str)
        if m:
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                return datetime.datetime(y, mo, d, tzinfo=datetime.timezone.utc)
            except Exception:
                return None
    return None


# ------------------ per-source parsers (best-effort) ------------------
# Each returns a list of dicts with title, link, summary, published


def parse_deepseek(html: str, base_url: str):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    # DeepSeek list often links to WeChat mp articles; collect anchors to mp.weixin.qq.com or /news links
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "mp.weixin.qq.com/s/" in href or "/news" in href:
            title = a.get_text(strip=True)
            if not title:
                continue
            items.append({"title": title, "link": requests.compat.urljoin(base_url, href), "summary": "", "published": ""})
    return items


def parse_moonshot(html: str, base_url: str):
    if "Welcome to nginx!" in html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "news" in href or "/news" in href:
            title = a.get_text(strip=True)
            if title:
                items.append({"title": title, "link": requests.compat.urljoin(base_url, href), "summary": "", "published": ""})
    return items


def parse_doubao(html: str, base_url: str):
    # Doubao is SPA-heavy - no static anchors often. Best-effort: look for window data scripts
    soup = BeautifulSoup(html, "html.parser")
    items = []
    # try anchors
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/update" in href or "update" in href or "/posts" in href:
            title = a.get_text(strip=True)
            if title:
                items.append({"title": title, "link": requests.compat.urljoin(base_url, href), "summary": "", "published": ""})
    return items


def parse_qwen(html: str, base_url: str):
    # Qwen pages often render client-side; fallback to visible anchors
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        title = a.get_text(strip=True)
        if title and len(title) > 5:
            items.append({"title": title, "link": requests.compat.urljoin(base_url, href), "summary": "", "published": ""})
    return items


def parse_xai(html: str, base_url: str):
    # x.ai may 502 sometimes; when accessible, try article tags
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for art in soup.find_all("article"):
        a = art.find("a", href=True)
        if a and a.get_text(strip=True):
            items.append({"title": a.get_text(strip=True), "link": requests.compat.urljoin(base_url, a["href"]), "summary": "", "published": ""})
    # fallback anchors
    if not items:
        for a in soup.find_all("a", href=True):
            t = a.get_text(strip=True)
            if t and len(t) > 6:
                items.append({"title": t, "link": requests.compat.urljoin(base_url, a["href"]), "summary": "", "published": ""})
    return items


def parse_google_gemini(html: str, base_url: str):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    # Google uses cards with class uni-nup__card
    for card in soup.select(".uni-nup__card"):
        a = card.find("a", href=True)
        if a and a.get_text(strip=True):
            items.append({"title": a.get_text(strip=True), "link": requests.compat.urljoin(base_url, a["href"]), "summary": card.get_text(separator=" ", strip=True), "published": ""})
    # hero
    for section in soup.select(".featured-article-cat-subcat-hero__section, .uni-blog-landing-hero"):
        a = section.find("a", href=True)
        if a and a.get_text(strip=True):
            items.append({"title": a.get_text(strip=True), "link": requests.compat.urljoin(base_url, a["href"]), "summary": section.get_text(separator=" ", strip=True), "published": ""})
    return items


def parse_perplexity(html: str, base_url: str):
    # Perplexity blog often load dynamic; fallback to anchors
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.find_all("a", href=True):
        t = a.get_text(strip=True)
        href = a["href"]
        if t and ("/" in href or len(t) > 20):
            items.append({"title": t, "link": requests.compat.urljoin(base_url, href), "summary": "", "published": ""})
    return items


PARSERS = {
    "https://deepseek.com/news": parse_deepseek,
    "https://www.moonshot.cn/news": parse_moonshot,
    "https://www.doubao.com/update": parse_doubao,
    "https://qwenlm.ai/blog/": parse_qwen,
    "https://x.ai/blog": parse_xai,
    "https://blog.google/technology/ai/": parse_google_gemini,
    "https://blog.perplexity.ai/": parse_perplexity,
}


TRANSLATOR_PROVIDERS = [
    ("GoogleTranslator", GoogleTranslator),
    ("MyMemoryTranslator", MyMemoryTranslator),
]


def translate_text_safe(text: str, target_lang: str, max_retries: int = 2) -> Tuple[str, bool, Optional[str]]:
    # returns (translated_text, success, error_message)
    if not text:
        return "", True, None

    errors = []
    for name, translator_cls in TRANSLATOR_PROVIDERS:
        attempt = 1
        while attempt <= max_retries:
            try:
                translator = translator_cls(source="auto", target=target_lang)
                out = translator.translate(text)
                return out, True, None
            except Exception:
                err = traceback.format_exc()
                short = err.splitlines()[-1] if err else "Unknown translation error"
                errors.append(f"{name} attempt {attempt}: {short}")
                if attempt < max_retries:
                    time.sleep(1 * attempt)
                attempt += 1
        # try next provider after max retries
    final_error = "; ".join(errors)
    eprint(color_text(f"Warning: translation failed after retries: {final_error}", COLOR_YELLOW))
    return text, False, final_error


def append_translation_log(traceback_str: str, source: str, link: str, field: str, original_text: str, translated_to: str = ""):
    """Append full traceback and context to logs/translation_errors.log and logs/translation_errors.jsonl

    The human-readable .log file is useful for quick inspection; the .jsonl file stores one JSON object per line for structured analysis.
    """
    try:
        logs_dir = os.path.join(BASE_DIR, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        # plain text log
        path = os.path.join(logs_dir, "translation_errors.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write("---\n")
            f.write(f"Time: {datetime.datetime.now(datetime.timezone.utc).isoformat()}\n")
            f.write(f"Source: {source}\n")
            f.write(f"Link: {link}\n")
            f.write(f"Field: {field}\n")
            if translated_to:
                f.write(f"TranslatedTo: {translated_to}\n")
            f.write("Original: \n")
            f.write((original_text or "") + "\n")
            f.write("Traceback:\n")
            f.write(traceback_str + "\n\n")
        # structured JSONL log (one JSON object per line)
        jsonl_path = os.path.join(logs_dir, "translation_errors.jsonl")
        entry = {
            "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": source,
            "link": link,
            "field": field,
            "translated_to": translated_to,
            "original": original_text or "",
            "traceback": traceback_str,
        }
        with open(jsonl_path, "a", encoding="utf-8") as jf:
            jf.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        # If logging fails, print a yellow warning but continue
        eprint(color_text(f"Warning: failed to write translation log: {e}", COLOR_YELLOW))


def to_markdown(entries: List[Dict[str, str]]) -> str:
    # include original and translated columns plus translation metadata
    lines = ["| Source | Published | Title (orig) | Title (translated) | Summary (orig) | Summary (translated) | Translated To | Translation OK | Translation Error | Link |",
             "|---|---|---|---|---|---|---:|:---:|---|---|"]
    for e in entries:
        src = e.get("source", "")
        pub = e.get("published", "")
        title_orig = (e.get("title", "") or "").replace("|", "\\|")
        title_tr = (e.get("title_translated", "") or "").replace("|", "\\|")
        summary_orig = (e.get("summary", "") or "").replace("|", "\\|")
        summary_tr = (e.get("summary_translated", "") or "").replace("|", "\\|")
        translated_to = e.get("translated_to", "")
        translation_ok = "Yes" if e.get("translation_ok") else "No"
        translation_err = (e.get("translation_error") or "").replace("|", "\\|")
        link = e.get("link", "")
        lines.append(f"| {src} | {pub} | {title_orig} | {title_tr} | {summary_orig} | {summary_tr} | {translated_to} | {translation_ok} | {translation_err} | {link} |")
    return "\n".join(lines)


def save_json(path: str, entries: List[Dict[str, str]]):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "items": entries}, f, ensure_ascii=False, indent=2)


# ------------------ main workflow ------------------

def main():
    parser = argparse.ArgumentParser(description="AI news aggregator")
    parser.add_argument("--list-sources", action="store_true", help="List configured sources and exit")
    parser.add_argument("--source", action="append", help="Only fetch named source (may be repeated)")
    parser.add_argument("--limit", type=int, default=5, help="Max items per source")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours (default 24)")
    parser.add_argument("--translate", action="store_true", help="Enable translation")
    parser.add_argument("--lang", type=str, default="zh-CN", help="Target language (zh-CN, zh-TW, en, ja)")
    parser.add_argument("--translate-to", type=str, default=None, help="(deprecated) Target language, highest priority if provided")
    parser.add_argument("--output", type=str, default=None, help="Optional output path (news.json or news.md)")
    args = parser.parse_args()

    # language selection
    target = args.translate_to or (args.lang if args.translate else None)
    if target and target not in SUPPORTED_LANGS:
        eprint(color_text(f"Unsupported language: {target}. Supported: {', '.join(SUPPORTED_LANGS)}", COLOR_RED))
        return 2

    try:
        sources = load_sources(SOURCES_FILE)
    except Exception as exc:
        eprint(color_text(f"Failed to load sources.json: {exc}", COLOR_RED))
        return 2

    if args.list_sources:
        print("Configured sources:")
        for s in sources:
            print(f"- {s.get('name')} ({s.get('url')}) - {s.get('type')}")
        return 0

    # select sources
    selected = []
    if args.source:
        names = set(args.source)
        for s in sources:
            if s.get("name") in names:
                selected.append(s)
        missing = names - set([s.get("name") for s in selected])
        for m in missing:
            eprint(color_text(f"Warning: unknown source requested: {m}", COLOR_YELLOW))
        if not selected:
            eprint(color_text("No valid sources selected.", COLOR_RED))
            return 2
    else:
        selected = sources

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=args.hours)
    session = build_session(timeout=DEFAULT_TIMEOUT)

    results: List[Dict[str, str]] = []

    # iterate with progress
    for src in tqdm(selected, desc="Sources", unit="source"):
        name = src.get("name")
        url = src.get("url")
        renderer = src.get("renderer", "").lower() == "selenium"
        try:
            # fetch list page or feed
            html = None
            if renderer:
                html = fetch_page_content(url, session, use_selenium=True)
            if html is None:
                session.headers["User-Agent"] = get_random_user_agent()
                resp = session.get(url, timeout=DEFAULT_TIMEOUT, verify=False)
                resp.raise_for_status()
                content = resp.content
            else:
                content = html.encode("utf-8")
            random_delay()
            items: List[Dict[str, str]] = []
            # if feed-like
            if url.endswith(".xml") or url.endswith("/feed/") or b"<rss" in content[:200].lower() or b"<feed" in content[:200].lower():
                items = parse_feed(content)
            else:
                parser_func = PARSERS.get(url)
                try:
                    text = content.decode("utf-8", errors="replace")
                except Exception:
                    text = content.decode("latin-1", errors="replace")
                if parser_func:
                    items = parser_func(text, url)
                else:
                    # generic list extraction: anchors with reasonable text
                    soup = BeautifulSoup(text, "html.parser")
                    for a in soup.find_all("a", href=True):
                        t = a.get_text(strip=True)
                        href = a["href"].strip()
                        if t and len(t) > 10 and ("/" in href or href.startswith("http")):
                            items.append({"title": t, "link": requests.compat.urljoin(url, href), "summary": "", "published": ""})

            if not items:
                eprint(color_text(f"Warning: no items extracted from {name} ({url})", COLOR_YELLOW))
                continue

            # per-item processing with limit
            added = 0
            for it in items:
                if added >= args.limit:
                    break
                link = it.get("link") or ""
                title = it.get("title") or ""
                summary = it.get("summary") or ""
                published = it.get("published") or ""
                pub_dt = parse_published_date(published)
                # if no parsed date, we keep (user asked: if no date -> default to include and mark date unknown)
                if pub_dt and pub_dt < cutoff:
                    continue

                # fetch article body & image (best-effort)
                body = ""
                image = ""
                if link:
                    html = None
                    if renderer:
                        html = fetch_page_content(link, session, use_selenium=True)
                    if html is None:
                        try:
                            session.headers["User-Agent"] = get_random_user_agent()
                            r2 = session.get(link, timeout=DEFAULT_TIMEOUT, verify=False)
                            r2.raise_for_status()
                            html = r2.content.decode("utf-8", errors="replace")
                        except Exception:
                            html = None
                    if html:
                        body, image = extract_article_body_and_image(html, link)
                    random_delay()

                # translation handling: keep original in title/summary, translated in *_translated
                title_translated = ""
                summary_translated = ""
                translated_ok = True
                translation_errors: List[str] = []
                if target:
                    title_translated, ok_t, err_t = translate_text_safe(title, target)
                    if not ok_t:
                        translated_ok = False
                        if err_t:
                            translation_errors.append(f"title: {err_t.splitlines()[-1] if err_t else ''}")
                            # append full traceback to log file for diagnostics
                            append_translation_log(err_t, name, link, "title", title, target or "")
                    if summary:
                        summary_translated, ok_s, err_s = translate_text_safe(summary, target)
                        if not ok_s:
                            translated_ok = False
                            if err_s:
                                translation_errors.append(f"summary: {err_s.splitlines()[-1] if err_s else ''}")
                                append_translation_log(err_s, name, link, "summary", summary, target or "")

                entry = {
                    "source": name,
                    "source_url": url,
                    "title": title,
                    "title_translated": title_translated,
                    "link": link,
                    "published": published or "日期未知",
                    "published_parsed": pub_dt.isoformat() if pub_dt else "",
                    "summary": summary,
                    "summary_translated": summary_translated,
                    "body": body,
                    "image_url": image,
                    "translation_ok": translated_ok,
                    "translated_to": target or "",
                    "translation_error": "; ".join(translation_errors) if translation_errors else None,
                }
                results.append(entry)
                added += 1
        except requests.RequestException as e:
            eprint(color_text(f"Error fetching {name} ({url}): {e}", COLOR_RED))
            continue
        except Exception as e:
            eprint(color_text(f"Unhandled error processing {name} ({url}): {e}", COLOR_RED))
            continue

    # output
    if not results:
        eprint(color_text("No entries found.", COLOR_YELLOW))
        return 1

    if args.output:
        path = args.output
        if path.endswith(".json"):
            save_json(path, results)
            print(color_text(f"Saved {len(results)} items to {path}", COLOR_GREEN))
        elif path.endswith(".md"):
            md = to_markdown(results)
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
            print(color_text(f"Saved {len(results)} items to {path}", COLOR_GREEN))
        else:
            # try both
            save_json(path + ".json", results)
            md = to_markdown(results)
            with open(path + ".md", "w", encoding="utf-8") as f:
                f.write(md)
            print(color_text(f"Saved {len(results)} items to {path}.json and {path}.md", COLOR_GREEN))
    else:
        # print to console
        for e in results:
            # prefer translated title for display if available
            if e.get("title_translated"):
                print(color_text(f"[{e['source']}] [{(target or '').upper()}] {e.get('title_translated')}", COLOR_CYAN))
                print(f"Title (orig): {e.get('title')}")
            else:
                print(color_text(f"[{e['source']}] {e.get('title')}", COLOR_CYAN))
            print(f"Link: {e['link']}")
            print(f"Published: {e['published']}")
            if e.get("image_url"):
                print(f"Image: {e['image_url']}")
            if e.get("summary_translated"):
                print(color_text("Summary (translated):", COLOR_GREEN), e.get("summary_translated"))
                if e.get("summary"):
                    print("Summary (orig):", e.get("summary"))
            elif e.get("summary"):
                print(f"Summary: {e.get('summary')}")
            if e.get("body"):
                print("Body:")
                print(e["body"][:1000])
            print("-" * 80)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

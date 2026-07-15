#!/usr/bin/env python3
import argparse, os, re, signal, sqlite3, subprocess, sys, textwrap, time, urllib.parse
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

DATA_DIR = os.path.expanduser("~/.local/share/darksearch")
DB_PATH = os.path.join(DATA_DIR, "data", "darksearch.db")
TOR_DIR = os.path.join(DATA_DIR, "tor-data")
TOR_PORT = 9050
PROXY = f"socks5h://127.0.0.1:{TOR_PORT}"
UA = "DarkSearch/1.0 (+https://github.com/anomalyco/opencode; research tool)"
MAX_PAGE_SIZE = 5 * 1024 * 1024  # 5MB


class DarkSearch:
    def __init__(self, db_path=DB_PATH, proxy=PROXY):
        self.db_path = db_path
        self.proxy = {"http": proxy, "https": proxy}
        self._ensure_db()

    def _ensure_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        db = sqlite3.connect(self.db_path)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA auto_vacuum=1")
        db.execute("""CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT,
            content TEXT,
            domain TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            status_code INTEGER
        )""")
        db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts
            USING fts5(title, content, content=pages, content_rowid=id)""")
        db.execute("""CREATE TABLE IF NOT EXISTS crawl_queue (
            url TEXT PRIMARY KEY,
            depth INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            added_at TEXT DEFAULT (datetime('now'))
        )""")
        db.commit()
        db.close()

    def fetch_render(self, url):
        tmpdir = os.path.join("/tmp", f"darksearch-chrome-{int(time.time())}")
        try:
            cmd = ["google-chrome", "--headless", "--disable-gpu", "--no-sandbox",
                   f"--user-data-dir={tmpdir}",
                   "--proxy-server=socks5://127.0.0.1:9050",
                   "--dump-dom", url]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                return {"error": f"chrome exit {r.returncode}: {r.stderr[:200]}"}, None
            text = r.stdout
            if not text.strip():
                return {"error": "chrome returned empty DOM"}, None
            soup = BeautifulSoup(text, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else None
            body = soup.get_text(separator=" ", strip=True)[:50000]
            domain = urllib.parse.urlparse(url).netloc
            db = sqlite3.connect(self.db_path)
            db.execute("INSERT OR REPLACE INTO pages (url, title, content, domain, status_code) VALUES (?,?,?,?,200)",
                       (url, title, body, domain))
            db.execute("INSERT OR REPLACE INTO pages_fts (rowid, title, content) VALUES ((SELECT id FROM pages WHERE url=?), ?, ?)",
                       (url, title, body))
            db.commit()
            db.close()
            return {"status": 200, "title": title, "size": len(body), "domain": domain, "render": True}, soup
        except subprocess.TimeoutExpired:
            return {"error": "chrome timeout (60s)"}, None
        except FileNotFoundError:
            return {"error": "google-chrome not found"}, None
        except Exception as e:
            return {"error": str(e)}, None

    def fetch(self, url, _retry=True):
        try:
            r = requests.get(url, proxies=self.proxy, timeout=60, headers={"User-Agent": UA},
                             stream=True)
            content_len = 0
            chunks = []
            for chunk in r.iter_content(chunk_size=65536, decode_unicode=True):
                if chunk:
                    content_len += len(chunk)
                    if content_len > MAX_PAGE_SIZE:
                        r.close()
                        return {"error": "content too large (>5MB)"}, None
                    chunks.append(chunk)
            r.close()
            text = "".join(chunks)
            soup = BeautifulSoup(text, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else None
            body = soup.get_text(separator=" ", strip=True)[:50000]  # ponyail: cap body at 50k chars
            domain = urllib.parse.urlparse(url).netloc

            db = sqlite3.connect(self.db_path)
            db.execute("INSERT OR REPLACE INTO pages (url, title, content, domain, status_code) VALUES (?,?,?,?,?)",
                       (url, title, body, domain, r.status_code))
            db.execute("INSERT OR REPLACE INTO pages_fts (rowid, title, content) VALUES ((SELECT id FROM pages WHERE url=?), ?, ?)",
                       (url, title, body))
            db.commit()
            db.close()
            return {"status": r.status_code, "title": title, "size": len(body), "domain": domain}, soup
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if _retry:
                time.sleep(5)
                return self.fetch(url, _retry=False)
            return {"error": f"timeout/connection: {type(e).__name__}"}, None
        except Exception as e:
            return {"error": str(e)}, None

    def search(self, query, limit=20):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute(
                "SELECT p.id, p.url, p.title, p.domain, p.fetched_at, p.status_code, "
                "rank FROM pages_fts JOIN pages p ON pages_fts.rowid = p.id "
                "WHERE pages_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError as e:
            return {"error": f"search error (check syntax): {e}"}
        finally:
            db.close()

    def list_pages(self, domain=None, limit=50):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        if domain:
            rows = db.execute("SELECT id, url, title, domain, fetched_at, status_code FROM pages WHERE domain=? ORDER BY fetched_at DESC LIMIT ?",
                              (domain, limit)).fetchall()
        else:
            rows = db.execute("SELECT id, url, title, domain, fetched_at, status_code FROM pages ORDER BY fetched_at DESC LIMIT ?",
                              (limit,)).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def status(self):
        db = sqlite3.connect(self.db_path)
        pages = db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        domains = db.execute("SELECT COUNT(DISTINCT domain) FROM pages").fetchone()[0]
        queue = db.execute("SELECT COUNT(*) FROM crawl_queue WHERE status='pending'").fetchone()[0]
        db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        db.close()
        return {"pages": pages, "domains": domains, "queue": queue, "db_size": db_size}


def crawl(ds, start_url, max_pages=100, max_depth=3, delay=2):
    db = sqlite3.connect(ds.db_path)
    db.execute("INSERT OR IGNORE INTO crawl_queue (url, depth) VALUES (?, 0)", (start_url,))
    db.commit()
    db.close()

    seen = {start_url}
    fetched = 0

    def signal_handler(sig, frame):
        print(f"\nInterrupted. Fetched {fetched} pages. State saved in crawl_queue.")
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)

    while fetched < max_pages:
        db = sqlite3.connect(ds.db_path)
        row = db.execute("SELECT url, depth FROM crawl_queue WHERE status='pending' ORDER BY depth ASC, added_at ASC LIMIT 1").fetchone()
        if not row:
            print("Crawl queue empty.")
            db.close()
            break
        url, depth = row
        if depth > max_depth:
            db.execute("UPDATE crawl_queue SET status='skipped' WHERE url=?", (url,))
            db.commit()
            db.close()
            continue

        db.execute("UPDATE crawl_queue SET status='fetched' WHERE url=?", (url,))
        db.commit()
        db.close()

        result, soup = ds.fetch(url)
        fetched += 1
        print(f"[{fetched}/{max_pages}] depth={depth} {url} → {result.get('status', result.get('error', '?'))}")

        if depth < max_depth and soup and "status" in result:
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                parsed = urllib.parse.urlparse(href)
                if parsed.netloc and parsed.netloc.endswith(".onion") and href not in seen:
                    seen.add(href)
                    db2 = sqlite3.connect(ds.db_path)
                    db2.execute("INSERT OR IGNORE INTO crawl_queue (url, depth, status) VALUES (?, ?, 'pending')",
                                (href, depth + 1))
                    db2.commit()
                    db2.close()

        if fetched < max_pages:
            time.sleep(delay + hash(url) % 3)  # ponyail: deterministic jitter

    print(f"Crawl complete. Fetched {fetched} pages.")


def ensure_tor():
    """Start Tor user process if not already running."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", TOR_PORT))
        s.close()
        return  # Tor already running
    except ConnectionRefusedError:
        pass

    os.makedirs(TOR_DIR, exist_ok=True)
    proc = subprocess.Popen(
        ["tor", "--DataDirectory", TOR_DIR, "--SOCKSPort", str(TOR_PORT),
         "--Log", "notice", "--RunAsDaemon", "0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    print(f"Starting Tor (PID {proc.pid}) on port {TOR_PORT}. Waiting up to 60s...")
    for i in range(60):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(("127.0.0.1", TOR_PORT))
            s.close()
            print("Tor ready.")
            return
        except ConnectionRefusedError:
            time.sleep(1)
    print("Timed out waiting for Tor. Try running: tor --SOCKSPort 9050 &")
    sys.exit(1)


DIRECTORIES = [
    "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/",
    "http://tor66sewwizhwfrnrphw53cthpvxj6hr4copuh6zd7lgkoo3cqcmj7yd.onion/",
    "http://haystak5njsmn2hqkewecpaxetahtwhsbsa64jom2k22z5afxhnpxfid.onion/",
    "http://dreadytofatroptsdj6io7l3xptbet6onoyno2yv7jicoxknyazubrad.onion/",
]


def discover(ds, directories=None):
    directories = directories or DIRECTORIES
    found = set()
    for url in directories:
        result, soup = ds.fetch(url)
        if not soup or "error" in result:
            print(f"  {url} → {result.get('error', 'no soup')}")
            continue
        print(f"  {url} → {result.get('status', '?')} ({result.get('title', '?')[:50]})")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            parsed = urllib.parse.urlparse(href)
            if parsed.netloc and parsed.netloc.endswith(".onion") and href not in found:
                found.add(href)
    if not found:
        print("No .onion links found on directory pages.")
        return
    db = sqlite3.connect(ds.db_path)
    db.executemany(
        "INSERT OR IGNORE INTO crawl_queue (url, depth, status) VALUES (?, 0, 'pending')",
        [(u,) for u in found]
    )
    db.commit()
    db.close()
    print(f"Added {len(found)} .onion URLs to crawl queue")


def main():
    ensure_tor()

    ap = argparse.ArgumentParser(prog="darksearch", description="Darknet search & crawl tool")
    ap.add_argument("--db", default=DB_PATH, help="SQLite DB path")

    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="Fetch & store a single .onion page")
    p.add_argument("url")
    p.add_argument("--render", action="store_true", help="Use Chrome headless (renders JS)")

    p = sub.add_parser("crawl", help="BFS crawl from a start URL")
    p.add_argument("url")
    p.add_argument("--pages", type=int, default=100)
    p.add_argument("--depth", type=int, default=3)
    p.add_argument("--delay", type=int, default=2)

    p = sub.add_parser("search", help="Full-text search crawled content")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("list", help="List crawled pages")
    p.add_argument("--domain")
    p.add_argument("--limit", type=int, default=50)

    sub.add_parser("status", help="Crawl stats")
    sub.add_parser("serve", help="Start MCP server (use mcp_server.py instead)")

    p = sub.add_parser("discover", help="Fetch known directory .onion sites, extract & queue links")
    p.add_argument("--pages", type=int, default=50, help="max pages to fetch from discovered links")
    p.add_argument("--delay", type=int, default=3)

    args = ap.parse_args()
    ds = DarkSearch(args.db)

    if args.command == "fetch":
        if args.render:
            result, _ = ds.fetch_render(args.url)
        else:
            result, _ = ds.fetch(args.url)
        for k, v in result.items():
            print(f"  {k}: {v}")

    elif args.command == "crawl":
        crawl(ds, args.url, args.pages, args.depth, args.delay)

    elif args.command == "search":
        results = ds.search(args.query, args.limit)
        if isinstance(results, dict) and "error" in results:
            print(f"Error: {results['error']}")
        elif not results:
            print("No results.")
        else:
            for r in results:
                print(f"  [{r['id']}] {(r['title'] or '?')[:60]} — {r['url']}")

    elif args.command == "list":
        for r in ds.list_pages(args.domain, args.limit):
            title = (r['title'] or '?')[:60]
            print(f"  [{r['id']}] {r['status_code']} {r['domain']} — {title}")

    elif args.command == "status":
        s = ds.status()
        for k, v in s.items():
            print(f"  {k}: {v}")

    elif args.command == "discover":
        discover(ds)

    elif args.command == "serve":
        print("Use: python3 /home/adi/.local/share/darksearch/mcp_server.py")


if __name__ == "__main__":
    main()

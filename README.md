# darknet-tor-search

CLI tool to search, fetch, and crawl `.onion` hidden services through the Tor network. Built-in full-text search (SQLite FTS5) and optional MCP server for LLM integration.

## Requirements

- Tor running locally (`tor --SOCKSPort 9050 &`)
- Python 3.8+
- `requests`, `beautifulsoup4`

Optional — renders JavaScript-heavy .onion sites:
- `google-chrome` (any Chromium-based browser)

## Install

```bash
pip install -r requirements.txt
chmod +x darksearch.py
```

## Usage

### Fetch a single .onion page

```bash
./darksearch.py fetch http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/

# With JS rendering via Chrome headless
./darksearch.py fetch http://tor66sewwizhwfrnrphw53cthpvxj6hr4copuh6zd7lgkoo3cqcmj7yd.onion/search520487719?q=test --render
```

### Search crawled content (FTS5)

```bash
./darksearch.py search "forum community"
./darksearch.py search "api key" --limit 10
```

### BFS crawl from a seed URL

```bash
./darksearch.py crawl http://tor66sewwizhwfrnrphw53cthpvxj6hr4copuh6zd7lgkoo3cqcmj7yd.onion/ --pages 50 --depth 2 --delay 3
```

### Discover .onion URLs from known directories

```bash
./darksearch.py discover
```

### List crawled pages

```bash
./darksearch.py list
./darksearch.py list --domain tor66sewwizhwfrnrphw53cthpvxj6hr4copuh6zd7lgkoo3cqcmj7yd.onion
```

### Stats

```bash
./darksearch.py status
```

### MCP Server (for LLM tools like opencode)

```bash
python3 mcp_server.py
```

Registers three tools: `darknet_fetch`, `darknet_search`, `darknet_status`.

## Database

SQLite with FTS5 — stored at `~/.local/share/darksearch/data/darksearch.db`.
Tor data at `~/.local/share/darksearch/tor-data/`.

## How it works

1. Spawns a Tor process if not already running (port 9050)
2. Routes all HTTP requests through `socks5h://127.0.0.1:9050`
3. Parses HTML with BeautifulSoup, indexes title + body into FTS5
4. `--render` flag uses `google-chrome --headless --dump-dom` for JS-rendered pages

## Disclaimer

For authorized security research and educational purposes only.

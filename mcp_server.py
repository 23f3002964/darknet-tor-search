#!/usr/bin/env python3
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from darksearch import DarkSearch, ensure_tor

ensure_tor()
ds = DarkSearch()


def main():
    from mcp.server import FastMCP
    server = FastMCP("darksearch")

    @server.tool()
    def darknet_fetch(url: str) -> str:
        result, _soup = ds.fetch(url)
        if "error" in result:
            return f"Error: {result['error']}"
        import sqlite3
        db = sqlite3.connect(ds.db_path)
        row = db.execute("SELECT content FROM pages WHERE url=?", (url,)).fetchone()
        db.close()
        content = (row[0][:2000] + "...") if row and row[0] else "(no content)"
        return f"Fetched {url}\nTitle: {result.get('title','?')}\nSize: {result['size']} chars\n\nFirst 2000 chars:\n{content}"

    @server.tool()
    def darknet_search(query: str, limit: int = 10) -> str:
        results = ds.search(query, limit)
        if isinstance(results, dict) and "error" in results:
            return f"Error: {results['error']}"
        if not results:
            return "No results."
        lines = []
        for r in results:
            lines.append(f"[{r['id']}] {(r['title'] or '?')[:60]} — {r['url']}")
        return "\n".join(lines)

    @server.tool()
    def darknet_status() -> str:
        s = ds.status()
        lines = [f"{k}: {v}" for k, v in s.items()]
        return "\n".join(lines)

    server.run()


if __name__ == "__main__":
    main()

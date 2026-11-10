"""Compatibility entry point for local-only web checks."""
import argparse
from pathlib import Path
from urllib.parse import urlsplit

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--url",help="Read-only smoke check against an already running local server")
    args=parser.parse_args()
    if not args.url:
        from qa_redesign import main as acceptance
        acceptance()
        return
    if urlsplit(args.url).hostname not in {"127.0.0.1","localhost"}:
        raise ValueError("Only a local RAG server is allowed")
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(channel="msedge",headless=True)
        page=browser.new_page(viewport={"width":1440,"height":1100})
        errors=[]
        page.on("pageerror",lambda error:errors.append(str(error)))
        for route,view in [("","query-view"),("/admin","admin-view")]:
            page.goto(args.url.rstrip("/")+route,wait_until="networkidle")
            expect(page.locator("#"+view)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size({"width":390,"height":844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors,errors
        browser.close()
    print("OK: local desktop/mobile pages; no mutations or model calls")

if __name__=="__main__":main()

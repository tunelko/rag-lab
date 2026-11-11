"""Synchronize selected public knowledge sources into the local structured store."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from knowledge_connectors import CONNECTORS, ConnectorError, HTTPClient
from knowledge_store import KnowledgeStore


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data" / "knowledge"


def status(store):
    return {
        "spaces": store.list_spaces(),
        "sources": store.list_sources(),
    }


def synchronize(data_dir, sources, *, timeout=30):
    data_dir = Path(data_dir).resolve()
    store = KnowledgeStore(data_dir / "knowledge.sqlite3")
    client = HTTPClient(timeout=timeout)
    ordered = [name for name in ("kev", "epss", "cve") if name in sources]
    return [asdict(CONNECTORS[name]().sync(store, data_dir / "raw", client=client))
            for name in ordered]


def parser():
    result = argparse.ArgumentParser(prog="python -m tools.sync_corpus", description=__doc__)
    result.add_argument("--data-dir", type=Path, default=DEFAULT_DATA,
                        help="Private local knowledge directory (default: data/knowledge).")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show local spaces, sources and published snapshots.")
    sync = commands.add_parser("sync", help="Download, validate and atomically publish sources.")
    sync.add_argument("--source", action="append", choices=tuple(CONNECTORS), required=True,
                      help="Source to synchronize; repeat for several sources.")
    sync.add_argument("--timeout", type=int, default=30,
                      help="HTTPS timeout per request in seconds (1..300).")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "status":
        path = args.data_dir.resolve() / "knowledge.sqlite3"
        payload = status(KnowledgeStore(path))
    else:
        if not 1 <= args.timeout <= 300:
            parser().error("--timeout must be between 1 and 300 seconds")
        try:
            payload = {"results": synchronize(
                args.data_dir, set(args.source), timeout=args.timeout,
            )}
        except ConnectorError as error:
            print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
            return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

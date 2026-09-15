"""Point the mediator at the laptops that host the sources, by rewriting base_url in SOURCE_CATALOG.

    python scripts/configure_cluster.py --show
    python scripts/configure_cluster.py --set REG=192.168.1.11 INS=192.168.1.12 \
                                              THEFT=192.168.1.13 CAM=192.168.1.14
    python scripts/configure_cluster.py --local          # put every source back on 127.0.0.1
    python scripts/configure_cluster.py --probe          # GET /health on each, report up/down

A value may be a bare host (192.168.1.11), host:port (192.168.1.11:8001) or a full base URL. With no
port, the source's conventional port is used. Only base_url changes; trust, authority, covers and
timeout are written back unchanged through mediator.catalog.register_source.

Run this on the mediator laptop only. The source laptops never need it: a wrapper does not know who
is calling it. meta.db is gitignored, so each laptop keeps its own copy and nobody's LAN addresses
are committed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # runnable as a plain script

import httpx  # noqa: E402

from mediator.catalog import get_source_catalog, register_source  # noqa: E402

DEFAULT_PORTS = {"REG": 8001, "INS": 8002, "THEFT": 8003, "CAM": 8004, "PUC": 8005}
PROBE_TIMEOUT_S = 2.0


def to_base_url(source_id: str, value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        raise SystemExit("empty address for " + source_id)
    if not value.startswith(("http://", "https://")):
        value = "http://" + value
    scheme, _, rest = value.partition("://")
    host = rest.split("/")[0]
    if ":" not in host:  # no port given: use this source's conventional one
        port = DEFAULT_PORTS.get(source_id)
        if port is None:
            raise SystemExit("no default port known for " + source_id + "; give host:port")
        host = host + ":" + str(port)
    return scheme + "://" + host


def apply(assignments: dict[str, str]) -> None:
    catalog = get_source_catalog()
    for source_id, value in assignments.items():
        meta = catalog.get(source_id)
        if meta is None:
            raise SystemExit("unknown source " + source_id + "; catalog has: " + ", ".join(catalog))
        base_url = to_base_url(source_id, value)
        register_source(source_id=source_id, display_name=meta["display_name"], dbms=meta["dbms"],
                        base_url=base_url, identifier_attr=meta["identifier_attr"],
                        authority=meta["authority"], trust_score=meta["trust_score"],
                        covers=meta.get("covers", []), timeout_ms=meta.get("timeout_ms", 1500))
        print("  " + source_id.ljust(6) + " -> " + base_url)


def show() -> None:
    catalog = get_source_catalog()
    if not catalog:
        print("SOURCE_CATALOG is empty - run: python -m mediator.catalog")
        return
    print("SRC    DBMS          BASE URL                         TIMEOUT  TRUST")
    for source_id in sorted(catalog):
        m = catalog[source_id]
        print(source_id.ljust(6) + " " + str(m["dbms"]).ljust(13) + " "
              + str(m["base_url"]).ljust(32) + " " + (str(m.get("timeout_ms", "")) + "ms").ljust(8)
              + " " + str(m.get("trust_score", "")))


def probe() -> int:
    catalog = get_source_catalog()
    if not catalog:
        print("SOURCE_CATALOG is empty")
        return 1
    down = 0
    for source_id in sorted(catalog):
        base_url = str(catalog[source_id]["base_url"]).rstrip("/")
        try:
            with httpx.Client(trust_env=False, timeout=PROBE_TIMEOUT_S) as client:
                response = client.get(base_url + "/health")
            body = response.json() if response.status_code == 200 else {}
            if response.status_code == 200 and body.get("up"):
                print("  UP    " + source_id.ljust(6) + " " + base_url
                      + "   (" + str(body.get("dbms", "?")) + ")")
                continue
            detail = str(body.get("error") or ("HTTP " + str(response.status_code)))
            print("  DOWN  " + source_id.ljust(6) + " " + base_url + "   " + detail)
        except Exception as exc:
            print("  DOWN  " + source_id.ljust(6) + " " + base_url
                  + "   " + type(exc).__name__ + ": " + str(exc).splitlines()[0][:90])
        down += 1
    print()
    print(str(len(catalog) - down) + "/" + str(len(catalog)) + " source(s) reachable")
    return 0 if down == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", nargs="+", metavar="SRC=ADDR", help="e.g. REG=192.168.1.11")
    parser.add_argument("--local", action="store_true", help="reset every source to 127.0.0.1")
    parser.add_argument("--show", action="store_true", help="print the catalog and exit")
    parser.add_argument("--probe", action="store_true", help="health-check every catalogued source")
    args = parser.parse_args(argv)

    if args.local:
        print("resetting every source to this machine:")
        apply({s: "127.0.0.1" for s in get_source_catalog()})
    if args.set:
        assignments = {}
        for item in args.set:
            if "=" not in item:
                raise SystemExit("expected SRC=ADDR, got " + item)
            source_id, _, value = item.partition("=")
            assignments[source_id.strip().upper()] = value
        print("updating source addresses:")
        apply(assignments)
    if args.probe:
        return probe()
    if args.show or not (args.set or args.local):
        show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

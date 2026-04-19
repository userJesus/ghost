"""CLI entry point for region selector — runs in a subprocess with main thread.

Prints JSON {x,y,w,h} to stdout on success, exits 1 on cancel.
"""
import json
import sys

from src.region_selector import select_region


def main():
    result = select_region()
    if result is None:
        sys.exit(1)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

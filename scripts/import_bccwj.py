import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from japanese_frequency.importers import import_bccwj


def main():
    parser = argparse.ArgumentParser(description="Import a BCCWJ LUW TSV or ZIP")
    parser.add_argument("source")
    parser.add_argument("--db-path")
    parser.add_argument("--version", default="1.0")
    arguments = parser.parse_args()
    result = import_bccwj(
        arguments.source, db_path=arguments.db_path, version=arguments.version
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from japanese_frequency.importers import import_jpdb


def main():
    parser = argparse.ArgumentParser(description="Import a JPDB frequency TSV")
    parser.add_argument("source")
    parser.add_argument("--db-path")
    parser.add_argument("--version", default="2.2")
    arguments = parser.parse_args()
    result = import_jpdb(
        arguments.source, db_path=arguments.db_path, version=arguments.version
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

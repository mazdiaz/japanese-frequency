import argparse
import json
import sys

from japanese_frequency.errors import JapaneseFrequencyError
from japanese_frequency.setup import setup_database


def _configure_utf8():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")


def _parser():
    parser = argparse.ArgumentParser(description="Initialize Japanese frequency data")
    parser.add_argument("--jpdb-source")
    parser.add_argument("--with-bccwj", action="store_true")
    parser.add_argument("--bccwj-source")
    parser.add_argument("--db", dest="db_path")
    return parser


def main(argv=None):
    _configure_utf8()
    arguments = _parser().parse_args(argv)
    try:
        report = setup_database(
            db_path=arguments.db_path,
            jpdb_source=arguments.jpdb_source,
            with_bccwj=arguments.with_bccwj,
            bccwj_source=arguments.bccwj_source,
        )
    except JapaneseFrequencyError as error:
        payload = {"error": {"type": error.code, "message": str(error)}}
        sys.stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return 1
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

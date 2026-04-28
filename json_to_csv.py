import argparse
import csv
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Convert a JSON file to CSV.")
    parser.add_argument("input", type=Path, help="Path to input .json file")
    parser.add_argument("output", type=Path, help="Path to output .csv file")
    args = parser.parse_args()

    with args.input.open(encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = [data]
    if not data:
        raise SystemExit("Input JSON is empty.")

    fieldnames = list({key for row in data for key in row.keys()})

    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)


if __name__ == "__main__":
    main()

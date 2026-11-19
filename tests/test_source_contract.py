import csv
import json
from pathlib import Path
import platform
import unittest

ROOT = Path(__file__).resolve().parents[1]


def read_table(name):
    with (ROOT / "data" / (name + ".csv")).open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        return reader.fieldnames, list(reader)


class SourceContract(unittest.TestCase):
    def test_python(self):
        self.assertEqual(platform.python_version(), "3.11.2")

    def test_source_files(self):
        file = Path(__file__).with_name("source_schema.json")
        contracts = json.loads(file.read_text(encoding="utf-8"))
        self.assertEqual(set(contracts), {file.stem for file in (ROOT / "data").glob("*.csv")})
        for name, (fields, keys) in contracts.items():
            with self.subTest(source=name):
                columns, rows = read_table(name)
                self.assertEqual(columns, fields)
                self.assertTrue(rows)
                self.assertTrue(all(None not in row and None not in row.values() for row in rows))
                if keys:
                    identities = [tuple(row[key] for key in keys) for row in rows]
                    self.assertTrue(all(all(identity) for identity in identities))
                    self.assertEqual(len(identities), len(set(identities)))

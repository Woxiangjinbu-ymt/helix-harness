import unittest

from app import handle


class ImportTest(unittest.TestCase):
    def test_modern_record(self):
        row = {"id": "a", "labels": ["one"]}
        self.assertEqual(handle({"record": row}), row)

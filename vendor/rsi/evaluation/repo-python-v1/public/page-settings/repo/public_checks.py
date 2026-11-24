import unittest

from app import handle


class PageTest(unittest.TestCase):
    def test_default_size(self):
        self.assertEqual(handle({"items": [4, 3, 2]}), {"items": [4, 3]})

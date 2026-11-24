import unittest

from app import handle


class ScheduleTest(unittest.TestCase):
    def test_unbounded_results_are_sorted(self):
        self.assertEqual(handle({"spans": [[8, 9], [1, 2]]}), {"available": [[1, 2], [8, 9]]})

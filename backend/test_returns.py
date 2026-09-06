import unittest
from datetime import date

from backend.returns import xirr


class XirrTests(unittest.TestCase):
    def test_excel_example(self):
        # https://support.microsoft.com/en-us/excel/functions/xirr-function
        flows = [('2008-01-01', -10000), ('2008-03-01', 2750),
                 ('2008-10-30', 4250), ('2009-02-15', 3250), ('2009-04-01', 2750)]
        self.assertAlmostEqual(xirr([(date.fromisoformat(d), v) for d, v in flows]), .373362535, places=8)

    def test_midyear_contribution(self):
        final = 1000 * 1.1 + 500 * 1.1 ** (183 / 365)
        self.assertAlmostEqual(xirr([(date(2021, 1, 1), -1000),
                                     (date(2021, 7, 2), -500),
                                     (date(2022, 1, 1), final)]), .1, places=10)

    def test_negative_and_zero_returns(self):
        for final, expected in [(800, -.2), (1000, 0)]:
            self.assertAlmostEqual(xirr([(date(2021, 1, 1), -1000),
                                         (date(2022, 1, 1), final)]), expected, places=10)

    def test_same_date_and_single_sign(self):
        self.assertIsNone(xirr([(date(2021, 1, 1), -100), (date(2021, 1, 1), 110)]))
        self.assertIsNone(xirr([(date(2021, 1, 1), 100), (date(2022, 1, 1), 110)]))

    def test_multiple_roots(self):
        self.assertIsNone(xirr([(date(2021, 1, 1), -100), (date(2022, 1, 1), 230),
                                (date(2023, 1, 1), -132)]))


if __name__ == '__main__':
    unittest.main()

import unittest
from leap import is_leap


class TestIsLeap(unittest.TestCase):
    def test_divisible_by_4(self):
        self.assertTrue(is_leap(2024))

    def test_not_divisible_by_4(self):
        self.assertFalse(is_leap(2023))

    def test_century_not_leap(self):
        self.assertFalse(is_leap(1900))  # 能被100整除但不能被400 → 不是闰年

    def test_century_leap(self):
        self.assertTrue(is_leap(2000))   # 能被400整除 → 是闰年


if __name__ == "__main__":
    unittest.main()

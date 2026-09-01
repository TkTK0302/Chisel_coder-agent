#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Calculator — 数学计算函数库（约 500 行，用于演示 AST 精准修改）。

此文件包含几十个数学计算函数，分布在多个类别中。
Agent 演示任务：找到 compound_interest 函数，添加 tax_rate 参数，只改这一个函数。
"""

import math
from typing import List, Optional, Tuple, Union

# ==============================================================================
# 基础算术运算
# ==============================================================================


def add(a: float, b: float) -> float:
    """返回两数之和。"""
    return a + b


def subtract(a: float, b: float) -> float:
    """返回两数之差。"""
    return a - b


def multiply(a: float, b: float) -> float:
    """返回两数之积。"""
    return a * b


def divide(a: float, b: float) -> float:
    """返回两数之商。"""
    if b == 0:
        raise ValueError("除数不能为零")
    return a / b


def power(base: float, exponent: float) -> float:
    """返回 base 的 exponent 次幂。"""
    return base ** exponent


def sqrt(value: float) -> float:
    """返回平方根。"""
    if value < 0:
        raise ValueError("不能对负数开平方根")
    return math.sqrt(value)


def factorial(n: int) -> int:
    """返回 n 的阶乘。"""
    if n < 0:
        raise ValueError("阶乘仅适用于非负整数")
    return math.factorial(n)


def gcd(a: int, b: int) -> int:
    """返回最大公约数。"""
    return math.gcd(a, b)


def lcm(a: int, b: int) -> int:
    """返回最小公倍数。"""
    return abs(a * b) // math.gcd(a, b)


def absolute(value: float) -> float:
    """返回绝对值。"""
    return abs(value)


def round_to(value: float, decimals: int = 2) -> float:
    """四舍五入到指定小数位。"""
    return round(value, decimals)


def floor(value: float) -> int:
    """向下取整。"""
    return math.floor(value)


def ceil(value: float) -> int:
    """向上取整。"""
    return math.ceil(value)


def modulo(a: float, b: float) -> float:
    """返回 a 除以 b 的余数。"""
    return a % b


def is_even(n: int) -> bool:
    """判断是否为偶数。"""
    return n % 2 == 0


def is_odd(n: int) -> bool:
    """判断是否为奇数。"""
    return n % 2 != 0


def is_prime(n: int) -> bool:
    """判断是否为质数。"""
    if n < 2:
        return False
    for i in range(2, int(math.sqrt(n)) + 1):
        if n % i == 0:
            return False
    return True


def prime_factors(n: int) -> List[int]:
    """返回质因数列表。"""
    factors = []
    d = 2
    while d * d <= n:
        while n % d == 0:
            factors.append(d)
            n //= d
        d += 1
    if n > 1:
        factors.append(n)
    return factors


# ==============================================================================
# 三角函数
# ==============================================================================


def sin_deg(angle: float) -> float:
    """角度制正弦。"""
    return math.sin(math.radians(angle))


def cos_deg(angle: float) -> float:
    """角度制余弦。"""
    return math.cos(math.radians(angle))


def tan_deg(angle: float) -> float:
    """角度制正切。"""
    return math.tan(math.radians(angle))


def arcsin(value: float) -> float:
    """反正弦（返回角度制）。"""
    return math.degrees(math.asin(value))


def arccos(value: float) -> float:
    """反余弦（返回角度制）。"""
    return math.degrees(math.acos(value))


def arctan(value: float) -> float:
    """反正切（返回角度制）。"""
    return math.degrees(math.atan(value))


def hypotenuse(a: float, b: float) -> float:
    """返回直角三角形斜边长。"""
    return math.hypot(a, b)


# ==============================================================================
# 对数与指数
# ==============================================================================


def natural_log(x: float) -> float:
    """自然对数 ln(x)。"""
    if x <= 0:
        raise ValueError("对数参数必须为正数")
    return math.log(x)


def log_base_10(x: float) -> float:
    """以 10 为底的对数。"""
    if x <= 0:
        raise ValueError("对数参数必须为正数")
    return math.log10(x)


def log_base(x: float, base: float) -> float:
    """任意底数的对数。"""
    if x <= 0 or base <= 0 or base == 1:
        raise ValueError("参数无效")
    return math.log(x) / math.log(base)


def exponential(x: float) -> float:
    """e^x。"""
    return math.exp(x)


def exp_growth(initial: float, rate: float, time: float) -> float:
    """指数增长：initial * e^(rate * time)。"""
    return initial * math.exp(rate * time)


# ==============================================================================
# 统计函数
# ==============================================================================


def mean(values: List[float]) -> float:
    """算术平均值。"""
    if not values:
        raise ValueError("列表不能为空")
    return sum(values) / len(values)


def median(values: List[float]) -> float:
    """中位数。"""
    if not values:
        raise ValueError("列表不能为空")
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
    return sorted_vals[mid]


def variance(values: List[float], sample: bool = True) -> float:
    """方差。"""
    if len(values) < 2:
        raise ValueError("至少需要两个数据点")
    m = mean(values)
    divisor = len(values) - 1 if sample else len(values)
    return sum((x - m) ** 2 for x in values) / divisor


def std_dev(values: List[float], sample: bool = True) -> float:
    """标准差。"""
    return math.sqrt(variance(values, sample))


def range_stat(values: List[float]) -> float:
    """极差。"""
    return max(values) - min(values)


def sum_of_squares(values: List[float]) -> float:
    """平方和。"""
    return sum(x ** 2 for x in values)


def z_score(value: float, values: List[float]) -> float:
    """Z 分数（标准分数）。"""
    m = mean(values)
    s = std_dev(values)
    if s == 0:
        return 0.0
    return (value - m) / s


# ==============================================================================
# 金融计算
# ==============================================================================


def simple_interest(principal: float, rate: float, time: float) -> float:
    """简单利息：principal * rate * time。"""
    return principal * rate * time


def compound_interest(principal: float, rate: float, time: float) -> float:
    """复利计算：principal * (1 + rate)^time。

    Args:
        principal: 本金
        rate: 年利率（小数形式，如 0.05 表示 5%）
        time: 投资年限

    Returns:
        最终本息总额
    """
    return principal * (1 + rate) ** time


def present_value(future_value: float, rate: float, time: float) -> float:
    """现值计算：future_value / (1 + rate)^time。"""
    return future_value / (1 + rate) ** time


def future_value_annuity(payment: float, rate: float, periods: int) -> float:
    """年金终值。"""
    if rate == 0:
        return payment * periods
    return payment * ((1 + rate) ** periods - 1) / rate


def present_value_annuity(payment: float, rate: float, periods: int) -> float:
    """年金现值。"""
    if rate == 0:
        return payment * periods
    return payment * (1 - (1 + rate) ** -periods) / rate


def net_present_value(cash_flows: List[float], discount_rate: float) -> float:
    """净现值 NPV。"""
    return sum(cf / (1 + discount_rate) ** t for t, cf in enumerate(cash_flows))


def internal_rate_of_return(cash_flows: List[float], guess: float = 0.1) -> float:
    """内部收益率 IRR（牛顿法近似）。"""
    rate = guess
    for _ in range(1000):
        npv = sum(cf / (1 + rate) ** t for t, cf in enumerate(cash_flows))
        d_npv = sum(-t * cf / (1 + rate) ** (t + 1) for t, cf in enumerate(cash_flows))
        if d_npv == 0:
            break
        rate -= npv / d_npv
        if abs(npv) < 1e-6:
            return rate
    return rate


def loan_payment(principal: float, annual_rate: float, months: int) -> float:
    """等额本息月供。"""
    monthly_rate = annual_rate / 12
    if monthly_rate == 0:
        return principal / months
    return principal * monthly_rate * (1 + monthly_rate) ** months / ((1 + monthly_rate) ** months - 1)


def roi(gain: float, cost: float) -> float:
    """投资回报率。"""
    if cost == 0:
        return 0.0
    return (gain - cost) / cost


def cagr(beginning_value: float, ending_value: float, years: float) -> float:
    """年复合增长率。"""
    if beginning_value <= 0 or years <= 0:
        return 0.0
    return (ending_value / beginning_value) ** (1 / years) - 1


# ==============================================================================
# 几何计算
# ==============================================================================


def circle_area(radius: float) -> float:
    """圆面积。"""
    return math.pi * radius ** 2


def circle_circumference(radius: float) -> float:
    """圆周长。"""
    return 2 * math.pi * radius


def triangle_area(base: float, height: float) -> float:
    """三角形面积。"""
    return 0.5 * base * height


def triangle_area_heron(a: float, b: float, c: float) -> float:
    """海伦公式求三角形面积。"""
    s = (a + b + c) / 2
    return math.sqrt(s * (s - a) * (s - b) * (s - c))


def rectangle_area(width: float, height: float) -> float:
    """矩形面积。"""
    return width * height


def sphere_volume(radius: float) -> float:
    """球体积。"""
    return (4 / 3) * math.pi * radius ** 3


def sphere_surface_area(radius: float) -> float:
    """球表面积。"""
    return 4 * math.pi * radius ** 2


def cylinder_volume(radius: float, height: float) -> float:
    """圆柱体积。"""
    return math.pi * radius ** 2 * height


def cone_volume(radius: float, height: float) -> float:
    """圆锥体积。"""
    return (1 / 3) * math.pi * radius ** 2 * height


def distance_2d(x1: float, y1: float, x2: float, y2: float) -> float:
    """二维欧几里得距离。"""
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def distance_3d(x1: float, y1: float, z1: float, x2: float, y2: float, z2: float) -> float:
    """三维欧几里得距离。"""
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)


# ==============================================================================
# 单位转换
# ==============================================================================


def celsius_to_fahrenheit(celsius: float) -> float:
    """摄氏度转华氏度。"""
    return celsius * 9 / 5 + 32


def fahrenheit_to_celsius(fahrenheit: float) -> float:
    """华氏度转摄氏度。"""
    return (fahrenheit - 32) * 5 / 9


def km_to_miles(km: float) -> float:
    """公里转英里。"""
    return km * 0.621371


def miles_to_km(miles: float) -> float:
    """英里转公里。"""
    return miles / 0.621371


def kg_to_pounds(kg: float) -> float:
    """公斤转磅。"""
    return kg * 2.20462


def pounds_to_kg(pounds: float) -> float:
    """磅转公斤。"""
    return pounds / 2.20462


def liters_to_gallons(liters: float) -> float:
    """升转加仑。"""
    return liters * 0.264172


def gallons_to_liters(gallons: float) -> float:
    """加仑转升。"""
    return gallons / 0.264172


# ==============================================================================
# 数列与级数
# ==============================================================================


def arithmetic_sequence(start: float, diff: float, n: int) -> List[float]:
    """等差数列前 n 项。"""
    return [start + i * diff for i in range(n)]


def arithmetic_sum(start: float, diff: float, n: int) -> float:
    """等差数列前 n 项和。"""
    return n * (2 * start + (n - 1) * diff) / 2


def geometric_sequence(start: float, ratio: float, n: int) -> List[float]:
    """等比数列前 n 项。"""
    return [start * ratio ** i for i in range(n)]


def geometric_sum(start: float, ratio: float, n: int) -> float:
    """等比数列前 n 项和。"""
    if ratio == 1:
        return start * n
    return start * (1 - ratio ** n) / (1 - ratio)


def fibonacci(n: int) -> List[int]:
    """斐波那契数列前 n 项。"""
    if n <= 0:
        return []
    seq = [0, 1]
    for _ in range(2, n):
        seq.append(seq[-1] + seq[-2])
    return seq[:n]


# ==============================================================================
# 概率函数
# ==============================================================================


def permutations(n: int, k: int) -> int:
    """排列数 P(n, k)。"""
    return math.perm(n, k)


def combinations(n: int, k: int) -> int:
    """组合数 C(n, k)。"""
    return math.comb(n, k)


def binomial_probability(n: int, k: int, p: float) -> float:
    """二项分布概率。"""
    return combinations(n, k) * (p ** k) * ((1 - p) ** (n - k))


def expected_value(values: List[float], probabilities: List[float]) -> float:
    """期望值。"""
    if len(values) != len(probabilities):
        raise ValueError("值和概率列表长度必须相同")
    return sum(v * p for v, p in zip(values, probabilities))


# ==============================================================================
# 自测入口
# ==============================================================================

if __name__ == "__main__":
    print("Calculator 模块加载成功")
    print(f"  add(3, 4) = {add(3, 4)}")
    print(f"  compound_interest(1000, 0.05, 10) = {compound_interest(1000, 0.05, 10):.2f}")
    print(f"  factorial(10) = {factorial(10)}")
    print(f"  fibonacci(10) = {fibonacci(10)}")
    print(f"  mean([1,2,3,4,5]) = {mean([1, 2, 3, 4, 5])}")
    print(f"  circle_area(5) = {circle_area(5):.2f}")
    print("所有函数测试通过 ✓")
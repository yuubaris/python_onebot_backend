# -*- coding: utf-8 -*-
"""货币换算与格式化工具。

换算关系：
    100 铜币 = 1 银币
    100 银币 = 1 金币
    100 金币 = 1 王国币
    100 王国币 = 1 圣王国币

内部统一以最小单位“铜币”存储，展示时再按上面规则拆分。
"""
SHENGWANGGUO = 100_000_000   # 1 圣王国币 = 100_000_000 铜币
WANGGUO = 1_000_000          # 1 王国币 = 1_000_000 铜币
JINBI = 10_000               # 1 金币 = 10_000 铜币
YINBI = 100                  # 1 银币 = 100 铜币


def format_currency(copper) -> str:
    """将铜币数量格式化为「圣王国币 王国币 金币 银币 铜币」的展示字符串。"""
    copper = int(copper or 0)
    if copper < 0:
        return "-" + format_currency(-copper)
    if copper == 0:
        return "0 铜币"

    parts = []
    sheng = copper // SHENGWANGGUO
    if sheng:
        parts.append(f"{sheng} 圣王国币")
    wang = (copper % SHENGWANGGUO) // WANGGUO
    if wang:
        parts.append(f"{wang} 王国币")
    jin = (copper % WANGGUO) // JINBI
    if jin:
        parts.append(f"{jin} 金币")
    yin = (copper % JINBI) // YINBI
    if yin:
        parts.append(f"{yin} 银币")
    tong = copper % YINBI
    if tong:
        parts.append(f"{tong} 铜币")
    return " ".join(parts)


def copper_to_parts(copper):
    """将铜币拆分为各货币单位数值，返回 dict。"""
    copper = int(copper or 0)
    return {
        "shengwangguobi": copper // SHENGWANGGUO,
        "wangguobi": (copper % SHENGWANGGUO) // WANGGUO,
        "jinbi": (copper % WANGGUO) // JINBI,
        "yinbi": (copper % JINBI) // YINBI,
        "tongbi": copper % YINBI,
    }

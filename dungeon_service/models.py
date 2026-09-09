# -*- coding: utf-8 -*-
"""数据模型（转发根 models，单一 db 实例）。

剥离阶段：根目录 models.py 为唯一数据层，包内复用（拆仓库时连同 db 初始化一起搬走）。
"""
from models import *  # noqa: F401,F403
from models import db, User, UserItem, UserOre, UserMaterial, UserConsumable, UserBoss, UserBuff, TurnItem  # noqa: F401

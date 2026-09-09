# -*- coding: utf-8 -*-
"""转发壳（地下城剥离中）：真实实现已迁移至 dungeon_service.forge，本文件仅做模块替身，
保证现有调用方（commands 等）零改动。拆仓库后删除本壳。"""
import sys
import dungeon_service.forge as _impl
sys.modules[__name__] = _impl

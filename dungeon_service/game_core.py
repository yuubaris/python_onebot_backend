# -*- coding: utf-8 -*-
"""GameCore 门面（剥离方案 §4.2 单进程架构）。

消息端（根目录 commands.py / bot 层）只与本门面交互：
- settle：结算（保守 = 命令触发，与现状一致，不做时间驱动）
- run_command：15 个地下城命令的直调入口（同进程，无 HTTP）
- take_events：取走待展示事件（Boss 战报队列）

未来拆独立进程时，本门面后接 §4.3 存档的 HTTP 契约（server.py）即可，游戏域零改动。
"""
from . import dungeon
from .commands import (
    cmd_bag, cmd_shop, cmd_buy, cmd_sell, cmd_class, cmd_promote, cmd_dungeon,
    cmd_forge, cmd_forge_shop, cmd_alchemy, cmd_use, cmd_challenge, cmd_boss,
    cmd_lottery, cmd_turn,
)

# 地下城命令别名表（与根目录 COMMANDS 的地下城部分一致）
_DUNGEON_COMMANDS = {
    "背包": cmd_bag, "bag": cmd_bag, "beibao": cmd_bag,
    "武器库": cmd_shop, "武器": cmd_shop, "wqp": cmd_shop,
    "武具店": cmd_shop, "shop": cmd_shop, "wujudian": cmd_shop,
    "购买": cmd_buy, "buy": cmd_buy, "goumai": cmd_buy,
    "出售": cmd_sell, "sell": cmd_sell, "chushou": cmd_sell,
    "转职": cmd_class, "class": cmd_class, "zhuanzhi": cmd_class,
    "晋升": cmd_promote, "promote": cmd_promote, "jinsheng": cmd_promote,
    "地下城": cmd_dungeon, "dungeon": cmd_dungeon, "dixiacheng": cmd_dungeon,
    "铁匠铺": cmd_forge_shop, "forgeshop": cmd_forge_shop, "tiejiangpu": cmd_forge_shop,
    "锻造": cmd_forge, "forge": cmd_forge, "duanzao": cmd_forge,
    "挑战": cmd_challenge, "challenge": cmd_challenge, "tiaozhan": cmd_challenge,
    "boss": cmd_boss, "bosslist": cmd_boss, "b": cmd_boss,
    "炼金": cmd_alchemy, "alchemy": cmd_alchemy, "lianjin": cmd_alchemy,
    "使用": cmd_use, "use": cmd_use, "shiyong": cmd_use,
    "转转": cmd_turn, "zhuanzhuan": cmd_turn, "zhuan": cmd_turn, "turn": cmd_turn,
    "抽奖": cmd_lottery, "lottery": cmd_lottery, "choujiang": cmd_lottery, "lucky": cmd_lottery,
}


class GameCore:
    """地下城游戏核心门面（单进程直调）。"""

    def settle(self, user):
        """命令触发结算（保守策略：与现状 dispatch_command 内 if in_dungeon: settle 一致）。"""
        return dungeon.settle_dungeon(user)

    def take_events(self, user_id):
        """取走并清空待展示事件（战报队列）。"""
        return dungeon.take_boss_report(user_id)

    def run_command(self, command, user, group_id, args="", at_qqs=None):
        """执行一个地下城命令；未知命令返回 None（由消息端兜底）。"""
        fn = _DUNGEON_COMMANDS.get(command)
        if fn is None:
            return None
        return fn(user, group_id, args, at_qqs)


game = GameCore()

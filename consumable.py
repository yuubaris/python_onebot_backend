# -*- coding: utf-8 -*-
"""炼金产物（药水/道具）注册表与使用模块（/使用 <物品名>）。

- 药水（potion）：/使用 后写入 UserBuff 属性加成（buff_stat），
  于 dungeon.effective_stats 并入（提升推进与 Boss 成功率）；
  持续口径：layers=推进 N 层失效 / time=N 秒自然到期。
- 道具（tool）：/使用 后写入 UserBuff 掉落增益（drop_bonus），
  掉落结算时按 bonus_type/scope 乘 mult；同样支持两种持续口径。

数据在 consumables.json；持有量在 user_consumable 表。
"""
import json
import os
import re
import time

from models import db, UserBuff, UserConsumable

CONSUMABLES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "consumables.json")

_cache = {"mtime": None, "items": [], "by_id": {}, "by_name": {}}

# 名称等级归一化：罗马/中文数字 → 阿拉伯数字（匹配兼容用）
# 注: lower() 会把全角罗马符号转小写变体(Ⅰ→ⅰ), 两套都要映射
_ROMAN_SYM = {"Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5, "Ⅵ": 6, "Ⅶ": 7, "Ⅷ": 8, "Ⅸ": 9, "Ⅹ": 10,
              "ⅰ": 1, "ⅱ": 2, "ⅲ": 3, "ⅳ": 4, "ⅴ": 5, "ⅵ": 6, "ⅶ": 7, "ⅷ": 8, "ⅸ": 9, "ⅹ": 10}
_ROMAN_ABC = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_TAIL_RE = re.compile(r"([0-9]+|[ivxlcdm]+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹ]+|[一二三四五六七八九十]+)$")


def normalize_name(s):
    """名称归一化：去全部空白 + 尾部等级（阿拉伯/罗马/中文数字）统一为阿拉伯数字，小写。

    使「聚财符 1」「聚财符1」「聚财符Ⅰ」「聚财符 I」「聚财符一」彼此等价；
    等级必须位于名称末尾（药水/道具命名均为「前缀+等级」结构）。
    """
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "", s)
    m = _TAIL_RE.search(s)
    if not m:
        return s
    tail = m.group(1)
    if re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹ]+$", tail):
        for r, n in sorted(_ROMAN_SYM.items(), key=lambda kv: -len(kv[0])):
            tail = tail.replace(r, str(n))
    elif re.match(r"^[ivxlcdm]+$", tail):
        for r, n in sorted(_ROMAN_ABC.items(), key=lambda kv: -len(kv[0])):
            tail = tail.replace(r, str(n))
    else:
        for c, n in _CN_NUM.items():
            tail = tail.replace(c, str(n))
    return s[: m.start()] + tail


def load_consumables(force=False):
    mtime = os.path.getmtime(CONSUMABLES_FILE)
    if not force and _cache["mtime"] == mtime:
        return _cache["items"]
    with open(CONSUMABLES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("consumables", [])
    _cache["mtime"] = mtime
    _cache["items"] = items
    _cache["by_id"] = {it["id"]: it for it in items}
    _cache["by_name"] = {it["name"]: it for it in items}
    return items


def find_consumable(name_or_id):
    load_consumables()
    key = (name_or_id or "").strip()
    if not key:
        return None
    if key in _cache["by_name"]:
        return _cache["by_name"][key]
    if key in _cache["by_id"]:
        return _cache["by_id"][key]
    low = key.lower()
    for iid, it in _cache["by_id"].items():
        if iid.lower() == low:
            return it
    for name, it in _cache["by_name"].items():
        if name.lower() == low:
            return it
    # 归一化匹配：兼容「名 1 / 名1 / 名Ⅰ / 名 I / 名一」等写法
    nk = normalize_name(key)
    for name, it in _cache["by_name"].items():
        if normalize_name(name) == nk:
            return it
    return None


def suggest_consumables(keyword, limit=5):
    load_consumables()
    kw = (keyword or "").strip()
    if not kw:
        return []
    kw2 = re.sub(r"\s+", "", kw)
    hits = [it for it in _cache["items"]
            if kw in it["name"] or kw in it["id"] or kw2 in re.sub(r"\s+", "", it["name"])]
    return hits[:limit]


def consumable_meta(item_id):
    load_consumables()
    return _cache["by_id"].get(item_id)


def grant_consumables(user_id, gained):
    """批量累加产物持有（gained: {item_id: count}）。"""
    if not gained:
        return
    for iid, cnt in gained.items():
        row = db.session.execute(
            db.select(UserConsumable).where(UserConsumable.user_id == user_id,
                                            UserConsumable.item_id == iid)
        ).scalars().first()
        if row is None:
            db.session.add(UserConsumable(user_id=user_id, item_id=iid, count=cnt))
        else:
            row.count += cnt


def owned_consumables(user_id, kind=None):
    """查询用户持有产物（可按 kind 过滤）：[{id,name,kind,level,effect,count}]。"""
    load_consumables()
    rows = db.session.execute(
        db.select(UserConsumable).where(UserConsumable.user_id == user_id,
                                        UserConsumable.count > 0)
    ).scalars().all()
    out = []
    for r in rows:
        meta = _cache["by_id"].get(r.item_id)
        if meta is None:
            continue
        if kind and meta.get("kind") != kind:
            continue
        out.append({"id": r.item_id, "name": meta["name"], "kind": meta.get("kind", "potion"),
                    "level": meta.get("level", 1), "effect": meta.get("effect", {}),
                    "count": r.count})
    out.sort(key=lambda it: (it["kind"], it["level"], it["id"]))
    return out


def consume_one(user_id, item_id):
    """扣 1 个产物；不足返回 False。"""
    row = db.session.execute(
        db.select(UserConsumable).where(UserConsumable.user_id == user_id,
                                        UserConsumable.item_id == item_id)
    ).scalars().first()
    if row is None or row.count < 1:
        return False
    row.count -= 1
    if row.count <= 0:
        db.session.delete(row)
    return True


def consume_layers(user_id, layers):
    """地下城推进 layers 层：扣减各层数型 buff 剩余层数（耗尽即清理）。"""
    if layers <= 0:
        return
    rows = db.session.execute(
        db.select(UserBuff).where(UserBuff.user_id == user_id)
    ).scalars().all()
    changed = False
    for r in rows:
        if r.remain_layers is None:
            continue
        r.remain_layers -= layers
        changed = True
        if r.remain_layers <= 0:
            db.session.delete(r)
    if changed:
        db.session.commit()


# ---------- 生效中 BUFF ----------

def active_buffs(user_id, now=None):
    """查询用户当前生效 BUFF 列表（自动剔除已到期时间型 / 已耗尽层数型）。"""
    now = now or time.time()
    rows = db.session.execute(
        db.select(UserBuff).where(UserBuff.user_id == user_id)
    ).scalars().all()
    load_consumables()
    out = []
    removed = False
    for r in rows:
        if r.expire_ts is not None and r.expire_ts <= now:
            db.session.delete(r)      # 时间型到期清理
            removed = True
            continue
        if r.remain_layers is not None and r.remain_layers <= 0:
            db.session.delete(r)      # 层数型已耗尽清理
            removed = True
            continue
        meta = _cache["by_id"].get(r.item_id)
        if meta is None:
            continue
        out.append((meta, r))
    if removed:
        db.session.commit()
    return out


def _layer_buff_stats(buffs):
    """汇总生效属性药水的属性加成（供 effective_stats 并入）。"""
    stats = {}
    for meta, row in buffs:
        eff = meta.get("effect", {})
        if eff.get("type") != "buff_stat":
            continue
        for k, v in (eff.get("stats") or {}).items():
            stats[k] = stats.get(k, 0) + v
    return stats


def buff_stat_bonus(user_id):
    """供 dungeon.effective_stats 调用：当前属性药水加成 {stat: value}。"""
    buffs = active_buffs(user_id)
    if not buffs:
        return {}
    return _layer_buff_stats(buffs)


def drop_bonus(user_id, bonus_type, scope=None, now=None):
    """供掉落结算调用：返回当前生效的对应掉落增益倍率（无则 1.0）。"""
    buffs = active_buffs(user_id, now=now)
    mult = 1.0
    for meta, _row in buffs:
        eff = meta.get("effect", {})
        if eff.get("type") != "drop_bonus":
            continue
        if eff.get("bonus_type") != bonus_type:
            continue
        if bonus_type == "material" and scope and eff.get("scope") not in (None, "all", scope):
            continue
        mult *= float(eff.get("mult", 1.0))
    return mult


# ---------- 使用 ----------

def use_item(user, item_id, now=None):
    """使用产物：先扣库存，成功再写入 UserBuff。返回 (ok, 提示文本)。

    - 药水/道具各自同一时间只能存在一种：使用新物品会替换同类的旧 BUFF，
      并刷新持续时长（层数型重置为完整层数 / 时间型重置为完整时长）；
    - 层数型以当前历史最高层为起始。
    """
    meta = consumable_meta(item_id)
    if meta is None:
        return False, "未知物品"
    # 先扣库存（不足则不改动任何 buff）
    if not consume_one(user.user_id, item_id):
        return False, "你没有该物品"

    now = now or time.time()
    eff = meta.get("effect", {})
    dur = eff.get("duration") or {}
    dtype = dur.get("type", "time")
    dval = int(dur.get("value", 0) or 0)
    kind = meta.get("kind", "potion")

    # 同槽位互斥：删除该用户全部同类旧 BUFF（药水槽 / 道具槽各一）
    replaced = []
    for r in db.session.execute(
        db.select(UserBuff).where(UserBuff.user_id == user.user_id)
    ).scalars().all():
        old = _cache["by_id"].get(r.item_id)
        if old is None or old.get("kind", "potion") != kind:
            continue
        if (r.expire_ts is not None and r.expire_ts <= now) or \
           (r.remain_layers is not None and r.remain_layers <= 0):
            db.session.delete(r)
            continue
        replaced.append(old.get("name", r.item_id))
        db.session.delete(r)

    from dungeon import historical_best_layer
    import json as _json
    row = UserBuff(
        user_id=user.user_id, item_id=item_id,
        effect_json=_json.dumps(eff, ensure_ascii=False),
    )
    if dtype == "layers":
        row.start_layer = historical_best_layer(user) or 0
        row.remain_layers = dval
    else:
        row.expire_ts = now + dval
    db.session.add(row)
    db.session.commit()

    rep_txt = f"（替换了原「{'、'.join(replaced)}」）" if replaced else ""
    if kind == "potion":
        stat_txt = "、".join(f"{k}+{v}" for k, v in (eff.get("stats") or {}).items())
        if dtype == "layers":
            return True, f"✨ 使用 {meta['name']}：临时提升 {stat_txt}（持续 {dval} 层）{rep_txt}"
        return True, f"✨ 使用 {meta['name']}：临时提升 {stat_txt}（持续 {dval} 秒）{rep_txt}"
    # tool
    if dtype == "layers":
        return True, f"✨ 使用 {meta['name']}：获得掉落增益（持续 {dval} 层）{rep_txt}"
    return True, f"✨ 使用 {meta['name']}：获得掉落增益（持续 {dval} 秒 ≈ {dval // 60} 分钟）{rep_txt}"


# ---------- 状态展示 ----------

def buffs_status_text(user_id, now=None):
    """状态命令展示：返回 (药水段, 道具段)，无则 None。"""
    buffs = active_buffs(user_id, now=now)
    if not buffs:
        return None, None
    now = now or time.time()
    potion_lines, tool_lines = [], []
    for meta, r in buffs:
        eff = meta.get("effect", {})
        name = meta.get("name", r.item_id)
        if meta.get("kind", "potion") == "potion":
            stats = "、".join(f"{k}+{v}" for k, v in (eff.get("stats") or {}).items())
            remain = f"{r.remain_layers} 层" if r.remain_layers is not None else "?"
            potion_lines.append(f"🧪 {name}：{stats}（剩余 {remain}）")
        else:
            desc = _drop_bonus_desc(eff)
            remain = _remain_time_text(r.expire_ts, now)
            tool_lines.append(f"🎫 {name}：{desc}（剩余 {remain}）")
    potion_txt = "\n".join(potion_lines) or None
    tool_txt = "\n".join(tool_lines) or None
    return potion_txt, tool_txt


def _drop_bonus_desc(eff):
    """把 drop_bonus effect 转成可读文本。"""
    btype = eff.get("bonus_type", "?")
    scope = eff.get("scope")
    mult = eff.get("mult", 1.0)
    if btype == "coin":
        t = "铜币掉落"
    elif btype == "equipment":
        t = "装备掉落"
    elif btype == "material":
        scope_txt = {"ore": "矿石", "herb": "草药", "special": "特殊材料",
                     "all": "全部材料"}.get(scope, "材料")
        t = f"{scope_txt}掉落"
    else:
        t = f"{btype}掉落"
    return f"{t} ×{mult:g}"


def _remain_time_text(expire_ts, now):
    """把到期时间戳转成「X 小时 Y 分 / Y 分钟」剩余文本。"""
    if not expire_ts:
        return "?"
    secs = max(0, int(expire_ts - now))
    h, m = divmod(secs // 60, 60)
    if h:
        return f"{h} 小时 {m} 分"
    return f"{m} 分钟"


def use_by_name(user, name):
    """按名称使用（/使用 <名>）。返回提示文本。"""
    meta = find_consumable(name)
    if meta is None:
        hits = suggest_consumables(name)
        hint = f"，你是不是想用：{'、'.join(h['name'] for h in hits)}" if hits else ""
        return f"没有「{name}」这种炼金物品{hint}\n发送 /炼金 查看配方与 /背包 查看持有。"
    ok, txt = use_item(user, meta["id"])
    return txt if ok else txt

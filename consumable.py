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
import time

from models import db, UserBuff, UserConsumable

CONSUMABLES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "consumables.json")

_cache = {"mtime": None, "items": [], "by_id": {}, "by_name": {}}


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
    return None


def suggest_consumables(keyword, limit=5):
    load_consumables()
    kw = (keyword or "").strip()
    if not kw:
        return []
    hits = [it for it in _cache["items"] if kw in it["name"] or kw in it["id"]]
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
    """使用产物：先扣库存，成功再写入/刷新 UserBuff。返回 (ok, 提示文本)。

    - 同名已有 buff：刷新时长（时间型重置 expire_ts；层数型累加 remain_layers）；
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

    # 查同名旧 buff（清理到期）
    existing = None
    for r in db.session.execute(
        db.select(UserBuff).where(UserBuff.user_id == user.user_id,
                                  UserBuff.item_id == item_id)
    ).scalars().all():
        if (r.expire_ts is not None and r.expire_ts <= now) or \
           (r.remain_layers is not None and r.remain_layers <= 0):
            db.session.delete(r)
            continue
        existing = r

    from dungeon import historical_best_layer
    if existing:
        # 刷新时长
        if dtype == "layers":
            existing.remain_layers = (existing.remain_layers or 0) + dval
        else:
            existing.expire_ts = now + dval
    else:
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

    kind = meta.get("kind", "potion")
    if kind == "potion":
        stat_txt = "、".join(f"{k}+{v}" for k, v in (eff.get("stats") or {}).items())
        if dtype == "layers":
            return True, f"✨ 使用 {meta['name']}：临时提升 {stat_txt}（持续 {dval} 层）"
        return True, f"✨ 使用 {meta['name']}：临时提升 {stat_txt}（持续 {dval} 秒）"
    # tool
    if dtype == "layers":
        return True, f"✨ 使用 {meta['name']}：获得掉落增益（持续 {dval} 层）"
    return True, f"✨ 使用 {meta['name']}：获得掉落增益（持续 {dval} 秒 ≈ {dval // 60} 分钟）"


def use_by_name(user, name):
    """按名称使用（/使用 <名>）。返回提示文本。"""
    meta = find_consumable(name)
    if meta is None:
        hits = suggest_consumables(name)
        hint = f"，你是不是想用：{'、'.join(h['name'] for h in hits)}" if hits else ""
        return f"没有「{name}」这种炼金物品{hint}\n发送 /炼金 查看配方与 /背包 查看持有。"
    ok, txt = use_item(user, meta["id"])
    return txt if ok else txt

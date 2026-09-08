# -*- coding: utf-8 -*-
"""「/踢 @B」头像合成模块。

A 用户发送 /踢 @B 时：
- 下载 A、B 两个 QQ 用户的头像（qqlogo 官方直链，失败用灰色占位图）
- 读取底图 images.jpg，将 A 头像 120x120 粘贴到 (75,34)（左上顶点定位），
  B 头像 120x120 粘贴到 (400,98)
- 保存到 <项目目录>/tmp/images_<时间戳>.jpg 并返回路径
- 每个用户 30 秒冷却
"""
import io
import os
import threading
import time
import urllib.request

from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_IMAGE = os.path.join(BASE_DIR, "images.jpg")
TMP_DIR = os.path.join(BASE_DIR, "tmp")   # 以当前目录为基础 ./tmp，非系统根目录

KICK_COOLDOWN_SECONDS = 30
# 底图布局：右侧熊猫(400,98)=踢人者(A)，左侧熊猫(75,34)=被踢者(B)
AVATAR_POS_A = (400, 98)    # A（踢人者）头像左上顶点
AVATAR_POS_B = (75, 34)     # B（被踢者）头像左上顶点
AVATAR_SIZE = 120

# 未知指令封禁图：beat.jpeg 底图 + 用户圆形头像 46x46 贴 (23,82)
BEAT_IMAGE = os.path.join(BASE_DIR, "beat.jpeg")
BEAT_AVATAR_POS = (23, 82)
BEAT_AVATAR_SIZE = 46

# /撅 多帧合成：3 帧序列底图（2886735798_frames/frame_000x.png）+ 每帧独立坐标
JUGE_FRAME_DIR = os.path.join(BASE_DIR, "2886735798_frames")
JUGE_FRAME_FILES = ["frame_0000.png", "frame_0001.png", "frame_0002.png"]
# 每帧 (B 坐标, A 坐标)，头像左上顶点定位，允许负值（边缘裁切）
JUGE_POSITIONS = [
    ((-4, 170), (117, -6)),
    ((10, 168), (108, 4)),
    ((4, 153), (129, -11)),
]
JUGE_AVATAR_SIZE = 120
JUGE_DURATION = 100   # 每帧时长（毫秒）

# /佬 合成：dalao.png 底图 + A/B 圆形头像 54x54（左上顶点定位）
DALAO_IMAGE = os.path.join(BASE_DIR, "dalao.png")
DALAO_AVATAR_POS_A = (91, 121)   # A（发起人）头像左上顶点
DALAO_AVATAR_POS_B = (200, 3)    # B（被 @ 者）头像左上顶点
DALAO_AVATAR_SIZE = 54

_AVATAR_URL = "https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_PLACEHOLDER_RGB = (170, 174, 180)  # 头像下载失败时的灰色占位

_cooldowns = {}
_cooldown_lock = threading.Lock()


def check_cooldown(user_id, seconds=KICK_COOLDOWN_SECONDS, key=None):
    """返回剩余冷却秒数；0 表示可用。key 用于区分不同功能的独立冷却。"""
    k = (user_id, key) if key else user_id
    with _cooldown_lock:
        last = _cooldowns.get(k, 0.0)
    remain = seconds - (time.time() - last)
    return remain if remain > 0 else 0.0


def mark_cooldown(user_id, key=None):
    k = (user_id, key) if key else user_id
    with _cooldown_lock:
        _cooldowns[k] = time.time()


def clear_cooldowns():
    with _cooldown_lock:
        _cooldowns.clear()


def make_circle(img, size=AVATAR_SIZE):
    """将头像裁成圆形并缩放到 size×size，返回 RGBA 图（圆形外为透明）。"""
    img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def download_avatar(qq, size=AVATAR_SIZE):
    """下载 QQ 头像并裁成圆形缩放到指定尺寸；失败返回灰色圆形占位头像。"""
    url = _AVATAR_URL.format(qq=qq)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        img = Image.new("RGBA", (200, 200), (*_PLACEHOLDER_RGB, 255))
    return make_circle(img, size)


def build_kick_image(a_qq, b_qq, base_image=BASE_IMAGE, out_dir=TMP_DIR,
                     avatar_loader=download_avatar):
    """合成「踢人」图并保存，返回绝对路径。"""
    if not os.path.exists(base_image):
        raise FileNotFoundError(f"底图不存在: {base_image}")
    base = Image.open(base_image).convert("RGB")
    avatar_a = avatar_loader(a_qq)   # 圆形 RGBA
    avatar_b = avatar_loader(b_qq)   # 圆形 RGBA
    base.paste(avatar_a, AVATAR_POS_A, avatar_a)
    base.paste(avatar_b, AVATAR_POS_B, avatar_b)
    os.makedirs(out_dir, exist_ok=True)
    ts = int(time.time() * 1000)
    out_path = os.path.join(out_dir, f"images_{ts}.jpg")
    base.save(out_path, "JPEG", quality=90)
    return out_path


def build_beat_image(qq, base_image=BEAT_IMAGE, out_dir=TMP_DIR,
                     avatar_loader=download_avatar):
    """合成「未知指令封禁」图：beat.jpeg 底图 + 用户圆形头像 46x46 贴 (23,82)，保存返回路径。"""
    if not os.path.exists(base_image):
        raise FileNotFoundError(f"底图不存在: {base_image}")
    base = Image.open(base_image).convert("RGB")
    avatar = avatar_loader(qq, size=BEAT_AVATAR_SIZE)
    base.paste(avatar, BEAT_AVATAR_POS, avatar)
    os.makedirs(out_dir, exist_ok=True)
    ts = int(time.time() * 1000)
    out_path = os.path.join(out_dir, f"beat_{ts}.jpg")
    base.save(out_path, "JPEG", quality=90)
    return out_path


def build_jue_image(a_qq, b_qq, out_dir=TMP_DIR, avatar_loader=download_avatar, flip=False):
    """合成「/撅 @B」多帧 GIF：3 帧序列底图，A/B 圆形头像按每帧坐标贴上。

    头像 120x120 圆形，每帧坐标见 JUGE_POSITIONS（左上顶点定位，允许负值边缘裁切），
    保存为 <tmp>/jue_<时间戳>.gif 并返回绝对路径。A=发起人，B=被撅者。
    flip=True 时 A/B 位置互换（B 撅了 A）：旋转后的 A 头像贴到 B 坐标、未旋转的 B 头像贴到 A 坐标。
    旋转规则：正常情况 B 旋转 90°；翻转情况 A 旋转 90°（撅人方旋转）。
    """
    if flip:
        # 翻转（B 撅 A）：A 旋转 90°（与正常时 B 同向）
        avatar_a = avatar_loader(a_qq, size=JUGE_AVATAR_SIZE).rotate(90)
        avatar_b = avatar_loader(b_qq, size=JUGE_AVATAR_SIZE)
    else:
        avatar_a = avatar_loader(a_qq, size=JUGE_AVATAR_SIZE)
        # B 头像圆形裁切后旋转 90°（用户认可的逆时针方向；圆形旋转后边界不变，无需 expand）
        avatar_b = avatar_loader(b_qq, size=JUGE_AVATAR_SIZE).rotate(90)
    frames = []
    for i, (b_pos, a_pos) in enumerate(JUGE_POSITIONS):
        path = os.path.join(JUGE_FRAME_DIR, JUGE_FRAME_FILES[i])
        if not os.path.exists(path):
            raise FileNotFoundError("帧底图不存在: %s" % path)
        frame = Image.open(path).convert("RGBA")
        if flip:
            frame.paste(avatar_b, a_pos, avatar_b)
            frame.paste(avatar_a, b_pos, avatar_a)
        else:
            frame.paste(avatar_b, b_pos, avatar_b)
            frame.paste(avatar_a, a_pos, avatar_a)
        frames.append(frame.convert("P", palette=Image.ADAPTIVE))
    os.makedirs(out_dir, exist_ok=True)
    ts = int(time.time() * 1000)
    out_path = os.path.join(out_dir, "jue_%d.gif" % ts)
    frames[0].save(out_path, "GIF", save_all=True, append_images=frames[1:],
                   duration=JUGE_DURATION, loop=0)
    return out_path


def build_dalao_image(a_qq, b_qq, base_image=DALAO_IMAGE, out_dir=TMP_DIR,
                      avatar_loader=download_avatar):
    """合成「/佬 @B」图：dalao.png 底图 + A/B 圆形头像 54x54，保存返回绝对路径。

    A（发起人）头像左上顶点 (91,121)，B（被 @ 者）头像左上顶点 (200,3)。
    """
    if not os.path.exists(base_image):
        raise FileNotFoundError(f"底图不存在: {base_image}")
    base = Image.open(base_image).convert("RGBA")
    avatar_a = avatar_loader(a_qq, size=DALAO_AVATAR_SIZE)   # 圆形 RGBA
    avatar_b = avatar_loader(b_qq, size=DALAO_AVATAR_SIZE)   # 圆形 RGBA
    base.paste(avatar_a, DALAO_AVATAR_POS_A, avatar_a)
    base.paste(avatar_b, DALAO_AVATAR_POS_B, avatar_b)
    os.makedirs(out_dir, exist_ok=True)
    ts = int(time.time() * 1000)
    out_path = os.path.join(out_dir, f"dalao_{ts}.png")
    base.save(out_path, "PNG")
    return out_path

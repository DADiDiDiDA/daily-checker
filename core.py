# -*- coding: utf-8 -*-
"""日常检查器核心逻辑：异常监控 / 日常检测 / 存疑判定 / 钉钉通知。

移植自 OneDragon ScriptChainer 的 检查日常.py，并把「命令行一次性运行」
改成可供 GUI 调用的引擎：逐游戏扫描、状态持久化、消息构建、钉钉推送。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

APP_ID = "DailyChecker"
APP_NAME = "日常检查器"
APP_VERSION = "1.7.2"

# ==================== 监控配置（与原脚本一致） ====================

BASE_DIR = r"D:\Automate\My-maawork"
DEFAULT_SCAN_DIRS = [BASE_DIR]      # 默认扫描的自动化程序根目录，可在设置里增删
TARGET_FOLDERS = ["on_error", "screencap"]
DEBUG_FOLDER_NAME = "debug"

TARGET_DESC_MAP: Dict[str, str] = {
    "on_error": "运行出错",
    "screencap": "捕获到错误截图",
}

# 程序文件夹「部分映射」：文件夹名里只要包含左侧关键词就算命中（不要求完全相等）。
# 关键词里不再保留各自动化程序共有的 MFAA 前缀，短词同样能命中 MFAAxxx 这种真实目录名。
FOLDER_NAME_MAP: Dict[str, str] = {
    # —— 非 MFAA 的自动化程序（直接放在 D:\Automate 下）——
    "BetterGI": "原神",
    "March7thAssistant": "星铁",
    "ZZZ-onedragon": "绝区零",
    "Maa_bbb": "崩坏三",
    "M9A": "1999",
    "MAA": "明日方舟",
    "MaaEnd": "终末地",
    # —— MFAA 系（放在 My-maawork 下，关键词去除了 MFAA 前缀）——
    "Watcher-of-Realms": "潮汐守望者",
    "E7": "第七史诗",
    "Resonance": "雷索纳斯",
    "AFK-Journey": "剑与远征启程",
    "Lily-Blade": "铃兰之剑",
    "FGO": "FGO",
    "TDJ": "天地劫",
    "Dragonheir": "龙息神迹",
    "Aligames": "坎公骑冠剑",
    "Langrisser": "梦幻模拟战",
    "Kes": "卡厄思梦境",
    "Mecharashi": "钢岚",
    "Xgame": "众神派对",
}

# 默认不内置任何真实凭证：新设备必须由使用者自己填 Webhook / 加签密钥。
# （旧配置走 load_config 的浅合并，已填好的用户配置不受影响。）
DEFAULT_DING = {
    "channel": "dingtalk",
    "enabled": False,
    "webhook": "",
    "secret": "",
}


@dataclass
class GameConfig:
    name: str
    folder: str                       # 自动化程序根目录（程序到它下面的日志文件夹里搜关键字）
    target: str                       # 成功关键字
    extension: Optional[str]
    success_message: str


# folder 一律填「自动化程序根目录」，日志文件夹由 DEFAULT_LOG_FOLDERS / 全局设置决定。
# 旧配置里如果还填到 logs / log / debug / .log 这一层，program_dir_of() 会自动往上退一层。
DAILY_GAMES: List[GameConfig] = [
    GameConfig("原神", r"D:\Automate\BetterGI", '检查每日奖励结果："今日奖励已领取"', ".log", "原神日常已完成！"),
    GameConfig("星铁", r"D:\Automate\March7thAssistant", "每日实训奖励完成", ".log", "星铁日常已完成！"),
    GameConfig("绝区零", r"D:\Automate\ZZZ-onedragon", "指令[ 活跃度奖励 ] 执行成功 返回状态 成功", None, "绝区零日常已完成！"),
    GameConfig("崩坏三", r"D:\Automate\Maa_bbb", "所有任务都已完成", ".log", "崩坏三日常已完成！"),
    GameConfig("1999", r"D:\Automate\M9A", "任务已全部完成", ".log", "1999日常已完成！"),
    GameConfig("明日方舟", r"D:\Automate\MAA", "任务已全部完成！", ".log", "明日方舟日常已完成！"),
    GameConfig("终末地", r"D:\Automate\MaaEnd", "MXU_KILLPROC", ".log", "终末地日常已完成！"),
    GameConfig("潮汐守望者", r"D:\Automate\My-maawork\MFAAWatcher-of-Realms", "任务已全部完成！", ".log", "潮汐守望者日常已完成！"),
    GameConfig("第七史诗", r"D:\Automate\My-maawork\MFAAE7", "任务已全部完成！", ".log", "第七史诗日常已完成！"),
    GameConfig("钢岚", r"D:\Automate\My-maawork\MFAAMecharashi", "任务已全部完成！", ".log", "钢岚日常已完成！"),
    GameConfig("雷索纳斯", r"D:\Automate\My-maawork\MFAAResonance", "任务已全部完成！", ".log", "雷索纳斯日常已完成！"),
    GameConfig("剑与远征启程", r"D:\Automate\My-maawork\MFAAAFK-Journey", "任务已全部完成！", ".log", "剑与远征启程日常已完成！"),
    GameConfig("铃兰之剑", r"D:\Automate\My-maawork\MFAALily-Blade", "任务已全部完成！", ".log", "铃兰之剑日常已完成！"),
    GameConfig("FGO", r"D:\Automate\My-maawork\MFAAFGO", "任务已全部完成！", ".log", "FGO日常已完成！"),
    GameConfig("天地劫", r"D:\Automate\My-maawork\MFAATDJ", "任务已全部完成！", ".log", "天地劫日常已完成！"),
    GameConfig("龙息神迹", r"D:\Automate\My-maawork\MFAADragonheir", "任务已全部完成！", ".log", "龙息神迹日常已完成！"),
    GameConfig("坎公骑冠剑", r"D:\Automate\My-maawork\MFAAAligames", "任务已全部完成！", ".log", "坎公骑冠剑日常已完成！"),
    GameConfig("梦幻模拟战", r"D:\Automate\My-maawork\MFAALangrisser", "任务已全部完成！", ".log", "梦幻模拟战日常已完成！"),
    GameConfig("卡厄思梦境", r"D:\Automate\My-maawork\MFAAKes", "任务已全部完成！", ".log", "卡厄思梦境日常已完成！"),
    GameConfig("众神派对", r"D:\Automate\My-maawork\MFAAXgame", "任务已全部完成！", ".log", "众神派对日常已完成！"),
]

DEFAULT_LOG_THRESHOLD_MB = 10
# 早期版本给 FGO / 潮汐守望者 / 钢岚 单独调过阈值（21 / 16 / 12）。现在统一成 10：
# 新设备上所有游戏都是 10；老配置里已经存下的单游戏阈值视为用户手动设置，原样保留。
DEFAULT_GAME_THRESHOLDS: Dict[str, int] = {}

# 「日志文件夹判定」：只在「自动化程序根目录」下的这几个文件夹里找关键字 / 检查目标文件，
# 每个文件夹只读顶层文件，不递归子目录，也不扫描程序里的其它文件夹。
# 注意：这里看的是自动化程序（BetterGI / MAA / MFAA 等）自己写出来的日志，不是游戏的日志。
DEFAULT_LOG_FOLDERS = ["logs", "log", "debug", ".log"]

# 首页关注列表的排序方式（默认：已修复倒数第二、存疑最后）
SUMMARY_SORTS = [
    ("state", "未完成优先"),
    ("name", "按名称"),
    ("fixed_last", "已修复置底"),
    ("default", "默认顺序"),
]
DEFAULT_SUMMARY_SORT = "state"

# 日常完成判断方式（第 1 种与原脚本一致）
# 说明文字要写清「看哪些文件 / 比什么 / 以什么为准」，别只写「关键词匹配」
METHODS = [
    ("keyword", "关键字（不限日期）",
     "在自动化程序目录下指定的日志文件夹里逐个打开 .log 读全文，"
     "内容里出现该游戏的完成关键字即算完成。不看文件日期，几天前的旧日志命中也会算。"),
    ("keyword_today", "关键字（仅今天的日志）",
     "同上，但只打开修改时间是今天的 .log 读全文，"
     "避免把昨天或前几天的成功记录当成本次完成。"),
    ("log_today", "今天有新日志",
     "不读日志内容：自动化程序的日志文件夹里只要有一个修改时间是今天的 .log 就算完成。"),
    ("file_exists", "特定文件已生成",
     "不读日志内容：目录里存在指定文件名的文件就算完成，不看日期。"
     "文件名写在规则里（「判定规则」页编辑规则时填）。"),
    ("file_today", "特定文件今天生成",
     "指定文件存在、且修改时间是今天，才算完成。文件名同样写在规则里。"),
]
METHOD_KEYS = tuple(k for k, _, _ in METHODS)
# 一句话版（列表/卡片里用，详细规则见 METHODS 的第三条与每行「匹配规则」）
FILE_METHODS = ("file_exists", "file_today")
DEFAULT_METHOD = "keyword"
# 方式短名（用于规则编辑器与规则列表的标签）
METHOD_SHORT = {
    "keyword": "关键词",
    "keyword_today": "今日关键词",
    "log_today": "今日新日志",
    "file_exists": "文件存在",
    "file_today": "今日文件",
}


# ==================== 判定规则库 ====================
# 规则 = 「怎么判断日常完成」：判定方式（可多选）+ 关键字 / 文件名 + 可选扩展名覆盖。
# 游戏只决定「看哪个自动化程序目录」，规则本身可以给多款游戏共用（例：MAA 系十几个
# 游戏都是「自动化程序日志里出现『任务已全部完成！』」）。改一条规则，引用的游戏全部跟着变。

BUILTIN_RULES: List[Dict] = [
    {"id": "r_genshin", "name": "原神 · 今日奖励已领取",
     "methods": ["keyword"], "keyword": '检查每日奖励结果："今日奖励已领取"',
     "file_target": "", "ext": "",
     "note": "BetterGI 日志里打印这一行即算完成"},
    {"id": "r_hsr", "name": "星铁 · 每日实训奖励完成",
     "methods": ["keyword"], "keyword": "每日实训奖励完成",
     "file_target": "", "ext": "",
     "note": "March7thAssistant 日志里的实训结算行"},
    {"id": "r_zzz", "name": "绝区零 · 活跃度奖励成功",
     "methods": ["keyword"], "keyword": "指令[ 活跃度奖励 ] 执行成功 返回状态 成功",
     "file_target": "", "ext": "",
     "note": "ZZZ-onedragon 的指令执行结果"},
    {"id": "r_bbb", "name": "崩坏三 · 所有任务都已完成",
     "methods": ["keyword"], "keyword": "所有任务都已完成",
     "file_target": "", "ext": "",
     "note": "Maa_bbb 收尾日志"},
    {"id": "r_m9a", "name": "1999 · 任务已全部完成",
     "methods": ["keyword"], "keyword": "任务已全部完成",
     "file_target": "", "ext": "",
     "note": "M9A 收尾日志；注意没有感叹号"},
    {"id": "r_maa", "name": "MAA 系 · 任务已全部完成！",
     "methods": ["keyword"], "keyword": "任务已全部完成！",
     "file_target": "", "ext": "",
     "note": "MAA / MFAA 系列脚本通用的完成日志，十几款游戏共用这一条"},
    {"id": "r_endfield", "name": "终末地 · MXU_KILLPROC",
     "methods": ["keyword"], "keyword": "MXU_KILLPROC",
     "file_target": "", "ext": "",
     "note": "MaaEnd 结束进程时打印的标记"},
]

# 内置游戏默认引用的规则；自定义游戏由界面显式指定
DEFAULT_RULE_MAP: Dict[str, str] = {
    "原神": "r_genshin",
    "星铁": "r_hsr",
    "绝区零": "r_zzz",
    "崩坏三": "r_bbb",
    "1999": "r_m9a",
    "明日方舟": "r_maa",
    "终末地": "r_endfield",
    "潮汐守望者": "r_maa",
    "第七史诗": "r_maa",
    "钢岚": "r_maa",
    "雷索纳斯": "r_maa",
    "剑与远征启程": "r_maa",
    "铃兰之剑": "r_maa",
    "FGO": "r_maa",
    "天地劫": "r_maa",
    "龙息神迹": "r_maa",
    "坎公骑冠剑": "r_maa",
    "梦幻模拟战": "r_maa",
    "卡厄思梦境": "r_maa",
    "众神派对": "r_maa",
}


def game_config(name: str, cfg: Optional[dict] = None) -> Optional[GameConfig]:
    """按游戏名取定义（先内置，再用户自定义）。"""
    for g in all_games(cfg):
        if g.name == name:
            return g
    return None


def all_games(cfg: Optional[dict] = None) -> List[GameConfig]:
    """全部游戏：内置清单 + 用户在界面里添加的游戏（同名覆盖内置）。"""
    out = list(DAILY_GAMES)
    if not cfg:
        return out
    for d in (cfg.get("check") or {}).get("custom_games") or []:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "").strip()
        if not name:
            continue
        ext = str(d.get("ext") or "").strip() or None
        g = GameConfig(name=name,
                       folder=str(d.get("folder") or "").strip(),
                       target=str(d.get("keyword") or d.get("target") or "").strip(),
                       extension=ext,
                       success_message=str(d.get("success_message") or "").strip()
                       or f"{name}日常已完成！")
        out = [x for x in out if x.name != name] + [g]
    return out


def _norm_rule(raw: dict) -> dict:
    """补齐一条规则的字段，丢掉不认识的判定方式。"""
    methods = raw.get("methods")
    if isinstance(methods, str):
        methods = [methods]
    methods = [m for m in (methods or []) if m in METHOD_KEYS] or [DEFAULT_METHOD]
    return {
        "id": str(raw.get("id") or "").strip(),
        "name": str(raw.get("name") or "").strip() or "未命名规则",
        "methods": methods,
        "keyword": str(raw.get("keyword") or "").strip(),
        "file_target": str(raw.get("file_target") or "").strip(),
        "ext": str(raw.get("ext") or "").strip(),
        "note": str(raw.get("note") or "").strip(),
    }


def get_rules(cfg: Optional[dict] = None) -> List[dict]:
    """规则库：配置里有就用配置的，否则用内置 7 条。"""
    stored = ((cfg or {}).get("check") or {}).get("rules")
    if isinstance(stored, list):
        rules = [_norm_rule(r) for r in stored if isinstance(r, dict)]
        rules = [r for r in rules if r["id"]]
        if rules:
            return rules
    return [_norm_rule(r) for r in BUILTIN_RULES]


def get_rule(cfg: Optional[dict], rule_id: str) -> Optional[dict]:
    rid = str(rule_id or "").strip()
    if not rid:
        return None
    return next((r for r in get_rules(cfg) if r["id"] == rid), None)


def new_rule_id(cfg: Optional[dict] = None, base: str = "r") -> str:
    used = {r["id"] for r in get_rules(cfg)}
    n = 1
    while f"{base}{n}" in used:
        n += 1
    return f"{base}{n}"


def save_rule(cfg: dict, rule: dict) -> dict:
    """新增或更新一条规则（按 id），返回落库后的规则。"""
    ck = cfg.setdefault("check", {})
    rules = [dict(r) for r in get_rules(cfg)]
    r = _norm_rule(rule)
    if not r["id"]:
        r["id"] = new_rule_id(cfg)
    for i, old in enumerate(rules):
        if old["id"] == r["id"]:
            rules[i] = r
            break
    else:
        rules.append(r)
    ck["rules"] = rules
    return r


def delete_rule(cfg: dict, rule_id: str) -> bool:
    """删掉一条规则；还有游戏在用时拒绝（返回 False）。"""
    if rule_usage(cfg, rule_id):
        return False
    rules = [r for r in get_rules(cfg) if r["id"] != rule_id]
    if len(rules) == len(get_rules(cfg)):
        return False
    cfg.setdefault("check", {})["rules"] = rules
    return True


def _legacy_rule_id(cfg: dict, game_name: str) -> str:
    """旧配置里的单游戏方式：如果和某条规则的判定方式、文件名、关键字完全一致，
    就直接认作引用了那条规则（界面里显示成规则，不再是一笔糊涂账）。"""
    ck = cfg.get("check") or {}
    if game_name not in (ck.get("methods") or {}):
        return ""
    raw = ck["methods"][game_name]
    ms = [raw] if isinstance(raw, str) else list(raw or [])
    ms = [m for m in ms if m in METHOD_KEYS]
    if not ms:
        return ""
    ft = str((ck.get("file_targets") or {}).get(game_name, "")).strip()
    g = game_config(game_name, cfg)
    kw = g.target if g else ""
    for r in get_rules(cfg):
        if (r["methods"] == ms and (r.get("file_target") or "") == ft
                and (not r.get("keyword") or r["keyword"] == kw)):
            return r["id"]
    return ""


def rule_id_for(cfg: dict, game_name: str) -> str:
    """该游戏引用的规则 id；空字符串表示「没绑定，沿用默认判断方式」。

    优先级：界面里显式绑定 > 旧版单游戏方式（能对上规则就认） > 内置默认规则。"""
    ck = cfg.get("check") or {}
    stored = ck.get("rule_map") or {}
    if game_name in stored:
        rid = str(stored.get(game_name) or "")
        # 规则被删掉后这里会留下悬空 id，认作「未绑定」更安全
        return rid if (not rid or get_rule(cfg, rid)) else ""
    legacy = _legacy_rule_id(cfg, game_name)
    if legacy:
        return legacy
    if game_name in (ck.get("methods") or {}):
        return ""                       # 旧配置里的自定义方式，交给 rule_of 合成
    return DEFAULT_RULE_MAP.get(game_name, "")


def rule_usage(cfg: dict, rule_id: str) -> List[str]:
    """哪些游戏在用这条规则（按当前解析结果算）。"""
    return [g.name for g in all_games(cfg)
            if rule_id and rule_id_for(cfg, g.name) == rule_id]


def rule_of(cfg: dict, game_name: str) -> dict:
    """该游戏实际生效的规则（可以是规则库里的，也可以是旧配置合成的）。"""
    rid = rule_id_for(cfg, game_name)
    if rid:
        r = get_rule(cfg, rid)
        if r:
            return r
    g = game_config(game_name, cfg)
    ck = cfg.get("check") or {}
    methods = ck.get("methods", {}).get(game_name) or get_default_methods(cfg)
    if isinstance(methods, str):
        methods = [methods]
    ft = str((ck.get("file_targets") or {}).get(game_name, "")).strip()
    return {"id": "", "name": "旧版自定义（未绑定规则）",
            "methods": [m for m in methods if m in METHOD_KEYS] or [DEFAULT_METHOD],
            "keyword": g.target if g else "", "file_target": ft, "ext": "",
            "note": "旧配置里的单游戏判断方式；在界面里绑一条规则即可统一管理"}


def rule_ext(rule: dict, game: Optional[GameConfig]) -> Optional[str]:
    """规则里的扩展名覆盖；为空则沿用游戏自己的扩展名。"""
    ext = str((rule or {}).get("ext") or "").strip()
    return ext or (game.extension if game else None)


def parse_exts(ext: Optional[str]) -> Optional[List[str]]:
    """把规则里的「扩展名」字段（逗号 / 空格 / 分号分隔，可带点）解析成小写后缀列表。

    空字符串 / None 表示「不过滤，扫描所有文件」。例如 ".log, .txt" → [".log", ".txt"]。
    短后缀会自动补点（"log" → ".log"）。"""
    if not ext or not str(ext).strip():
        return None
    out: List[str] = []
    for part in re.split(r"[,\s;]+", str(ext).strip()):
        part = part.strip().lower()
        if not part:
            continue
        if not part.startswith("."):
            part = "." + part
        if part not in out:
            out.append(part)
    return out or None


def rule_keyword(rule: dict, game: Optional[GameConfig] = None) -> str:
    kw = str((rule or {}).get("keyword") or "").strip()
    return kw or (game.target if game else "")


def describe_rule(keyword: str, folder: str, ext: Optional[str],
                  methods: List[str], file_target: str = "",
                  with_folder: bool = True, folders: Optional[List[str]] = None) -> str:
    """把判断方式翻译成一句可核对的具体规则：看哪些文件、比什么、以什么为准。

    with_folder=False 时用「自动化程序日志」代替完整路径（界面里一行放得下）。"""
    exts = parse_exts(ext)
    ext_txt = (" " + "、".join(f"*{e}" for e in exts) + " ") if exts else "文件"
    if with_folder:
        where, lead = f"{folder} 下", "读 "
    else:
        where, lead = "自动化程序日志下", "读"
    kw = f"「{keyword}」" if keyword else "完成关键字"
    out = []
    for m in methods:
        if m == "keyword":
            out.append(f"{lead}{where}所有{ext_txt}的全文，出现{kw}即算完成（不看日期）")
        elif m == "keyword_today":
            out.append(f"{lead}{where}今天修改过的{ext_txt}的全文，出现{kw}即算完成")
        elif m == "log_today":
            out.append(f"{where}只要有一个今天修改过的{ext_txt}就算完成（不读内容）")
        elif m == "file_exists":
            out.append(f"{where}存在「{file_target or '未设置文件名'}」就算完成（不看日期）")
        elif m == "file_today":
            out.append(f"{where}「{file_target or '未设置文件名'}」存在且今天修改过才算完成")
    text = "；或 ".join(out)
    if folders:
        text += f"；只查自动化程序目录下的 {'、'.join(folders)} 日志文件夹，不递归子目录"
    return text


def rule_text(cfg: dict, game: GameConfig, rule: Optional[dict] = None,
              with_folder: bool = True) -> str:
    """把「规则 + 自动化程序目录」翻译成一句可核对的话。"""
    r = rule or rule_of(cfg, game.name)
    return describe_rule(rule_keyword(r, game), game.folder, rule_ext(r, game),
                         r.get("methods") or [DEFAULT_METHOD],
                         r.get("file_target", ""), with_folder,
                         get_log_folders(cfg) if log_folder_check_enabled(cfg) else None)


def rule_name_of(cfg: dict, game_name: str) -> str:
    """该游戏引用的规则名（没绑规则时给出明确说明）。"""
    rid = rule_id_for(cfg, game_name)
    r = get_rule(cfg, rid) if rid else None
    return r["name"] if r else "未绑定（沿用默认判断方式）"


def describe_failure(keyword: str, ext: Optional[str], methods: List[str],
                     file_target: str, scanned: int, folder_exists: bool) -> str:
    """未完成时的一句话原因：比了什么关键字、读了几个什么文件。"""
    exts = parse_exts(ext)
    ext_txt = "、".join(f"*{e}" for e in exts) if exts else "文件"
    kw = keyword if any(m in ("keyword", "keyword_today") for m in methods) else ""
    ft = file_target if any(m in FILE_METHODS for m in methods) else ""
    parts = []
    if kw:
        parts.append(f"未命中「{kw}」")
    if ft:
        if "file_today" in methods and scanned and not kw:
            parts.append(f"「{ft}」不是今天生成的")
        else:
            parts.append(f"「{ft}」未生成")
    if not parts:
        if "log_today" in methods:
            return f"今天还没有新的 {ext_txt}"
        parts.append("未满足判断方式")
    head = " 或 ".join(parts)
    if not folder_exists:
        return f"{head} · 自动化程序日志文件夹不存在"
    if ft and not kw:
        return head                     # 文件方式只比文件，不用报扫描数
    if scanned:
        return f"{head} · 扫了 {scanned} 个 {ext_txt}"
    if "keyword_today" in methods:
        return f"{head} · 今天没有可读的 {ext_txt}（昨天的不算）"
    return f"{head} · 目录里没有可读的 {ext_txt}"


# 排序方式：常规排序与特殊排序分两类；特殊排序选「不启用」时用常规排序
SORT_MODES_BASIC = [
    ("default", "默认顺序"),
    ("name", "按名称 A-Z"),
    ("streak", "无错天数"),
    ("logsize", "日志大小"),
]
SORT_MODES_SPECIAL = [
    ("none", "不启用"),
    ("failed_first", "未完成优先"),
    ("error_first", "异常优先"),
    ("manual", "手动拖拽"),
]
DEFAULT_SORT_SPECIAL = "failed_first"
# 兼容旧调用：全部排序键合集
DEFAULT_SORT = "default"


# ==================== 游戏条目 ====================

@dataclass
class Game:
    key: str
    name: str
    folder: str
    target: str
    extension: Optional[str]
    success_message: str
    program_dir: str = ""
    is_maawork: bool = False
    # 检查结果
    daily: str = "unknown"            # done | failed | unknown
    detail: str = ""                  # 扫描说明 / 错误信息
    files_scanned: int = 0
    rule: str = ""                    # 当前判断方式的完整规则说明（工具提示用）
    log_size: int = 0                 # debug 目录顶层 .log 合计字节
    threshold_mb: int = DEFAULT_LOG_THRESHOLD_MB
    suspicious: bool = False
    error_flags: Set[str] = field(default_factory=set)   # on_error / screencap
    streak: int = 0                   # 连续无错天数
    state: str = "idle"               # idle | running | done
    scanned: bool = False
    excluded: bool = False            # 用户手动排除，不参与检查

    @property
    def has_error(self) -> bool:
        return bool(self.error_flags)

    @property
    def debug_dir(self) -> str:
        return os.path.join(self.program_dir, DEBUG_FOLDER_NAME) if self.program_dir else ""


# ==================== 配置与路径 ====================

def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_dir() -> str:
    return app_dir()


def cache_dir() -> str:
    return os.path.join(app_dir(), "config")


def config_path() -> str:
    return os.path.join(cache_dir(), "config.json")


def open_in_explorer(path: str) -> None:
    try:
        if os.path.isdir(path):
            subprocess.Popen(["explorer", os.path.normpath(path)])
        elif os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    except Exception:
        pass




def load_config() -> dict:
    cfg = {
        "ui": {"preset": "acrylic", "theme": "auto", "blur": True},
        "background": {"close_to_tray": True, "check_enabled": False,
                       "interval_min": 720, "auto_notify": True},
        "dingtalk": dict(DEFAULT_DING),
        "check": {"threshold_mb": DEFAULT_LOG_THRESHOLD_MB,
                  "thresholds": dict(DEFAULT_GAME_THRESHOLDS),
                  "cache_dir": "",
                  "excluded": [],
                  "methods": {},
                  "rules": None,           # None = 用内置规则库
                  "rule_map": {},
                  "custom_games": [],
                  "default_method": DEFAULT_METHOD,
                  "error_check": True,
                  "log_threshold_check": True,   # 是否启用日志体积阈值（存疑）检查
                  "streak_check": True,          # 是否统计 / 展示无错天数
                  "log_folder_check": True,      # 是否启用根目录日志文件夹判定
                  "log_folders": list(DEFAULT_LOG_FOLDERS),
                  "fixed_marks": {},             # 关注列表里手动标记「已修复」的游戏
                  "summary_sort": DEFAULT_SUMMARY_SORT,
                  "folder_map": {},
                  "folder_map_removed": [],
                  "file_targets": {},
                  "sort_mode": DEFAULT_SORT,
                  "sort_special": DEFAULT_SORT_SPECIAL,
                  "manual_order": []},
    }
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            stored = json.load(f)
        for k, v in stored.items():
            if k in cfg and isinstance(cfg[k], dict) and isinstance(v, dict):
                cfg[k].update(v)
            elif k in cfg and isinstance(cfg[k], dict) and v is None:
                # 配置里写 null（手动改坏/旧版导出）会导致 .get() 崩溃，保留默认值
                continue
            else:
                cfg[k] = v
    except FileNotFoundError:
        pass
    except Exception:
        # 配置文件损坏时先留一份备份，别让下一次保存直接把原内容抹掉
        try:
            import shutil
            shutil.copy2(config_path(), config_path() + ".corrupt")
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    """原子写配置：临时文件 + os.replace，避免写一半崩溃留下截断 JSON 导致下次启动配置丢失。"""
    os.makedirs(cache_dir(), exist_ok=True)
    p = config_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def rule_text_bare(rule: dict) -> str:
    """规则库列表里的一句话说明（不涉及具体游戏的目录）。"""
    return describe_rule(rule.get("keyword", ""), "", rule.get("ext") or None,
                         rule.get("methods") or [DEFAULT_METHOD],
                         rule.get("file_target", ""), with_folder=False)


def set_game_rule(cfg: dict, game_name: str, rule_id: str) -> None:
    """把游戏绑定到某条规则；rule_id 为空表示解绑（沿用默认判断方式）。"""
    ck = cfg.setdefault("check", {})
    rm = dict(ck.get("rule_map") or {})
    rm[game_name] = str(rule_id or "")
    ck["rule_map"] = rm
    # 显式绑定后，旧版的单游戏方式覆盖就不再参与了
    (ck.get("methods") or {}).pop(game_name, None)
    (ck.get("file_targets") or {}).pop(game_name, None)


def add_game(cfg: dict, name: str, folder: str, ext: str = ".log",
             success_message: str = "", rule_id: str = "",
             keyword: str = "") -> bool:
    """添加（或覆盖）一款自定义游戏，并绑定规则。"""
    name = str(name or "").strip()
    folder = str(folder or "").strip()
    if not name or not folder:
        return False
    ck = cfg.setdefault("check", {})
    items = [d for d in (ck.get("custom_games") or []) if isinstance(d, dict)]
    items = [d for d in items if str(d.get("name") or "").strip() != name]
    items.append({"name": name, "folder": folder,
                  "ext": str(ext or "").strip(),
                  "success_message": str(success_message or "").strip(),
                  "keyword": str(keyword or "").strip()})
    ck["custom_games"] = items
    if rule_id:
        set_game_rule(cfg, name, rule_id)
    return True


def remove_game(cfg: dict, name: str) -> bool:
    """移除自定义游戏，并清掉它在各处留下的引用（内置游戏不可移除）。"""
    if any(g.name == name for g in DAILY_GAMES):
        return False
    ck = cfg.setdefault("check", {})
    items = [d for d in (ck.get("custom_games") or []) if isinstance(d, dict)]
    left = [d for d in items if str(d.get("name") or "").strip() != name]
    if len(left) == len(items):
        return False
    ck["custom_games"] = left
    (ck.get("rule_map") or {}).pop(name, None)
    (ck.get("methods") or {}).pop(name, None)
    (ck.get("file_targets") or {}).pop(name, None)
    (ck.get("thresholds") or {}).pop(name, None)
    ck["excluded"] = [x for x in (ck.get("excluded") or []) if x != name]
    ck["manual_order"] = [x for x in (ck.get("manual_order") or []) if x != name]
    return True


def get_threshold_mb(cfg: dict, game_name: str) -> int:
    """指定游戏的日志体积阈值（MB）：单游戏覆盖 > 全局默认。"""
    overrides = (cfg.get("check") or {}).get("thresholds") or {}
    default = int((cfg.get("check") or {}).get("threshold_mb", DEFAULT_LOG_THRESHOLD_MB))
    try:
        return int(overrides.get(game_name, default))
    except Exception:
        return default


def set_threshold_mb(cfg: dict, game_name: str, mb: int) -> None:
    """设置单游戏日志阈值；等于默认值就删掉覆盖（回到跟随默认）。"""
    ck = cfg.setdefault("check", {})
    default = int(ck.get("threshold_mb", DEFAULT_LOG_THRESHOLD_MB))
    thr = dict(ck.get("thresholds") or {})
    mb = int(mb)
    if mb == default:
        thr.pop(game_name, None)
    else:
        thr[game_name] = mb
    ck["thresholds"] = thr




def get_methods(cfg: dict, game_name: str) -> List[str]:
    """该游戏生效的判断方式列表（任一满足即算完成），来自它引用的规则。"""
    return list(rule_of(cfg, game_name)["methods"])


def get_file_target(cfg: dict, game_name: str) -> str:
    """file_exists / file_today 方式要检测的文件名（相对自动化程序日志文件夹），来自规则。"""
    return str(rule_of(cfg, game_name)["file_target"]).strip()


def get_sort_mode(cfg: dict) -> str:
    """常规排序（默认顺序 / A-Z / 无错天数 / 日志大小）。"""
    m = str((cfg.get("check") or {}).get("sort_mode", DEFAULT_SORT))
    return m if m in dict((k, t) for k, t in SORT_MODES_BASIC) else DEFAULT_SORT


def get_sort_special(cfg: dict) -> str:
    """特殊排序（未完成优先 / 异常优先 / 手动拖拽），「none」表示不启用。

    默认未完成优先。"""
    s = str((cfg.get("check") or {}).get("sort_special", ""))
    if s in dict((k, t) for k, t in SORT_MODES_SPECIAL):
        return s
    # 旧版本把特殊排序存在 sort_mode 里，迁移过来
    old = str((cfg.get("check") or {}).get("sort_mode", ""))
    return old if old in ("failed_first", "error_first", "manual") else DEFAULT_SORT_SPECIAL




def get_notify_channels(cfg: dict) -> List[str]:
    """启用的通知方式列表，可多选：dingtalk（钉钉机器人）/ windows（系统通知）。

    channels 字段存过（包括空列表 = 全关）即以其为准；
    缺失时兼容旧配置，按单值 channel / enabled 推断。"""
    ding = cfg.get("dingtalk") or {}
    raw = ding.get("channels")
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list):
        return [c for c in raw if c in ("dingtalk", "windows")]
    ch = str(ding.get("channel", ""))
    if ch in ("dingtalk", "windows"):
        # 旧配置单值 channel 分支也必须尊重 enabled：明确关闭就一条都不发
        return [ch] if ding.get("enabled", True) else []
    return ["dingtalk"] if ding.get("enabled", True) else []




def get_manual_order(cfg: dict) -> List[str]:
    raw = (cfg.get("check") or {}).get("manual_order") or []
    return [str(x) for x in raw]


def sort_items(items: list, mode: str, manual_order: List[str] = None) -> list:
    """按排序方式返回新列表；未扫描的条目在数值排序里排在未扫扇区后面。"""
    items = list(items)
    if mode == "name":
        return sorted(items, key=lambda i: i.name)
    if mode == "streak":
        return sorted(items, key=lambda i: (-int(i.streak or 0), i.name))
    if mode == "logsize":
        return sorted(items, key=lambda i: (-int(i.log_size or 0), i.name))
    if mode == "failed_first":
        # 未完成优先；未完成里带异常（onerror 等）的再排最前
        def _tier(i):
            failed = i.scanned and i.daily == "failed" and not i.excluded
            if failed and i.has_error:
                return 0
            if failed:
                return 1
            if i.has_error:
                return 2
            return 3
        return sorted(items, key=lambda i: (_tier(i), i.name))
    if mode == "error_first":
        return sorted(items, key=lambda i: (
            0 if i.has_error else 1,
            0 if (i.scanned and i.daily == "failed" and not i.excluded) else 1, i.name))
    if mode == "manual" and manual_order:
        pos = {n: idx for idx, n in enumerate(manual_order)}
        return sorted(items, key=lambda i: (pos.get(i.name, 10_000), i.name))
    return items          # default: 保持 DAILY_GAMES 定义顺序


def get_default_method(cfg: dict) -> str:
    m = str((cfg.get("check") or {}).get("default_method", DEFAULT_METHOD))
    return m if m in METHOD_KEYS else DEFAULT_METHOD


def get_default_methods(cfg: dict) -> List[str]:
    """默认判断方式列表（可多个，任一满足即算完成）。

    优先取 check.default_methods；没有则回落到单值 default_method。"""
    ck = cfg.get("check") or {}
    raw = ck.get("default_methods")
    if isinstance(raw, str):
        raw = [raw]
    keys = [m for m in (raw or []) if m in METHOD_KEYS]
    if keys:
        return keys
    return [get_default_method(cfg)]


def get_folder_map(cfg: dict) -> Dict[str, str]:
    """文件夹关键词 → 游戏显示名；内置 13 条可编辑 / 可删，用户配置叠加其上。"""
    ck = cfg.get("check") or {}
    merged = dict(FOLDER_NAME_MAP)
    for k in (ck.get("folder_map_removed") or []):
        merged.pop(str(k), None)
    extra = ck.get("folder_map") or {}
    for k, v in extra.items():
        k, v = str(k).strip(), str(v).strip()
        if k and v:
            merged[k] = v
    return merged


def set_folder_map(cfg: dict, mapping: dict) -> Dict[str, str]:
    """把界面编辑后的「完整映射表」写回配置。

    内置条目被删 / 改名时记进 folder_map_removed，否则下次启动又被内置表补回来。
    """
    ck = cfg.setdefault("check", {})
    clean: Dict[str, str] = {}
    for k, v in (mapping or {}).items():
        k, v = str(k).strip(), str(v).strip()
        if k and v:
            clean[k] = v
    removed = sorted(k for k in FOLDER_NAME_MAP if k not in clean)
    extra = {k: v for k, v in clean.items()
             if k not in FOLDER_NAME_MAP or FOLDER_NAME_MAP[k] != v}
    if removed:
        ck["folder_map_removed"] = removed
    else:
        ck.pop("folder_map_removed", None)
    ck["folder_map"] = extra
    return clean


def reset_folder_map(cfg: dict) -> Dict[str, str]:
    """恢复内置 13 条映射（清掉所有自定义与删除记录）。"""
    ck = cfg.setdefault("check", {})
    ck.pop("folder_map_removed", None)
    ck["folder_map"] = {}
    return dict(FOLDER_NAME_MAP)


def folder_keyword_hit(name: str, folder_map: dict) -> bool:
    """部分映射：文件夹名里只要包含任一关键词就算命中（大小写不敏感）。"""
    low = str(name or "").lower()
    return any(k and str(k).lower() in low for k in folder_map)


def resolve_folder_name(name: str, folder_map: dict) -> str:
    """把程序文件夹名解析成游戏显示名：完全相等优先，否则取最长的命中关键词。

    最长优先是为了避免短关键词（如 E7 / FGO）抢走更具体的匹配。
    """
    if name in folder_map:
        return folder_map[name]
    low = str(name or "").lower()
    best = ""
    for k in folder_map:
        kk = str(k)
        if kk and kk.lower() in low and len(kk) > len(best):
            best = kk
    return folder_map.get(best, name) if best else name


def get_scan_dirs(cfg: dict) -> List[str]:
    """要扫描的自动化程序根目录（默认只有 My-maawork）。"""
    raw = (cfg.get("check") or {}).get("scan_dirs")
    if raw is None:
        return list(DEFAULT_SCAN_DIRS)
    out = []
    for x in raw or []:
        s = str(x).strip()
        if s and s not in out:
            out.append(s)
    return out


def set_scan_dirs(cfg: dict, dirs: List[str]) -> List[str]:
    """写入扫描根目录；等于默认值时删掉配置项，保持配置文件干净。"""
    ck = cfg.setdefault("check", {})
    clean: List[str] = []
    for d in dirs or []:
        s = os.path.normpath(str(d).strip()) if str(d).strip() else ""
        if s and s not in clean:
            clean.append(s)
    if clean == [os.path.normpath(x) for x in DEFAULT_SCAN_DIRS]:
        ck.pop("scan_dirs", None)
    else:
        ck["scan_dirs"] = clean
    return clean


def get_scan_excludes(cfg: dict) -> List[str]:
    """扫描时要跳过的子目录（完整路径，或只写文件夹名）。"""
    raw = (cfg.get("check") or {}).get("scan_excludes") or []
    out: List[str] = []
    for x in raw:
        s = str(x).strip()
        if s and s not in out:
            out.append(s)
    return out


def set_scan_excludes(cfg: dict, items: List[str]) -> List[str]:
    ck = cfg.setdefault("check", {})
    clean: List[str] = []
    for x in items or []:
        s = str(x).strip()
        if not s:
            continue
        s = os.path.normpath(s) if (os.sep in s or "/" in s) else s
        if s not in clean:
            clean.append(s)
    if clean:
        ck["scan_excludes"] = clean
    else:
        ck.pop("scan_excludes", None)
    return clean


def is_excluded_dir(path: str, excludes: Optional[List[str]] = None,
                    cfg: Optional[dict] = None) -> bool:
    """子目录是否被排除：完整路径按路径比，单写名字的按文件夹名比。"""
    items = excludes if excludes is not None else get_scan_excludes(cfg or {})
    name = os.path.basename(os.path.normpath(path)).casefold()
    p = os.path.normpath(path).casefold()
    for x in items:
        s = str(x).strip()
        if not s:
            continue
        if os.sep in s or "/" in s:
            if os.path.normpath(s).casefold() == p:
                return True
        elif s.casefold() == name:
            return True
    return False


def get_target_folders(cfg: dict) -> List[str]:
    """异常监控要检测的文件夹名（on_error / screencap 等）。"""
    raw = (cfg.get("check") or {}).get("target_folders")
    if raw is None:
        return list(TARGET_FOLDERS)
    out = []
    for x in raw:
        s = str(x).strip()
        if s and s not in out:
            out.append(s)
    return out


def error_check_enabled(cfg: dict) -> bool:
    return bool((cfg.get("check") or {}).get("error_check", True))


def log_threshold_check_enabled(cfg: dict) -> bool:
    """是否启用日志体积阈值检查（关掉后不再产生「存疑」）。"""
    return bool((cfg.get("check") or {}).get("log_threshold_check", True))


def streak_check_enabled(cfg: dict) -> bool:
    """是否统计 / 展示无错天数（关掉后不清零、不累加、不写通知里的天数段）。"""
    return bool((cfg.get("check") or {}).get("streak_check", True))


def log_folder_check_enabled(cfg: dict) -> bool:
    return bool((cfg.get("check") or {}).get("log_folder_check", True))


def parse_folder_names(raw) -> List[str]:
    """清洗「日志文件夹名」输入：只留单层文件夹名，挡掉路径与 . / ..。"""
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    else:
        s = str(raw or "").replace("，", ",").replace("、", ",").replace(";", ",")
        parts = s.split(",")
    out: List[str] = []
    for x in parts:
        name = str(x).strip().strip("/\\")
        if not name or name in (".", "..") or os.sep in name or "/" in name or "\\" in name:
            continue
        if name.lower() not in {y.lower() for y in out}:
            out.append(name)
    return out


def get_log_folders(cfg: dict) -> List[str]:
    """生效的日志文件夹名列表（全局设置；规则不再单独指定目录）。"""
    raw = (cfg.get("check") or {}).get("log_folders")
    if raw is None:
        return list(DEFAULT_LOG_FOLDERS)
    return parse_folder_names(raw)


def set_log_folders(cfg: dict, folders) -> List[str]:
    """写入全局日志文件夹列表；等于默认值时删掉配置，保持文件干净。"""
    clean = parse_folder_names(folders)
    ck = cfg.setdefault("check", {})
    if clean == DEFAULT_LOG_FOLDERS:
        ck.pop("log_folders", None)
    else:
        ck["log_folders"] = clean
    return clean


def program_dir_of(folder: str, cfg: Optional[dict] = None) -> str:
    """由「自动化程序目录」推出程序根目录。

    新版直接填程序根目录（...\\MFAAFGO），原样返回；旧版填的是日志子目录
    （...\\MFAAFGO\\logs），自动往上退一层，让老配置里的自定义游戏不用手动迁移。
    """
    p = os.path.normpath(str(folder or "").strip())
    if not p:
        return ""
    names = {x.lower() for x in DEFAULT_LOG_FOLDERS}
    if cfg:
        names |= {x.lower() for x in get_log_folders(cfg)}
    return os.path.dirname(p) if os.path.basename(p).lower() in names else p


# ---------- 关注列表：手动标记「已修复」+ 排序 ----------

def get_fixed_marks(cfg: dict) -> Dict[str, str]:
    raw = (cfg.get("check") or {}).get("fixed_marks") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if str(k).strip()}


def is_fixed(cfg: dict, game) -> bool:
    name = game if isinstance(game, str) else game.name
    return name in get_fixed_marks(cfg)


def set_fixed(cfg: dict, name: str, on: bool) -> bool:
    """标记只是加个状态，不把游戏移出关注列表；再点一次即可取消。"""
    ck = cfg.setdefault("check", {})
    marks = get_fixed_marks(cfg)
    if on:
        marks[name] = time.strftime("%Y-%m-%d %H:%M:%S")
    else:
        marks.pop(name, None)
    ck["fixed_marks"] = marks
    return bool(on)


def get_summary_sort(cfg: dict) -> str:
    mode = str((cfg.get("check") or {}).get("summary_sort") or DEFAULT_SUMMARY_SORT)
    return mode if mode in dict(SUMMARY_SORTS) else DEFAULT_SUMMARY_SORT


def sort_summary_items(items: list, cfg: dict, mode: str = "") -> list:
    """关注列表排序。默认「未完成优先」：未完成 → 异常 → 其它 → 已修复（倒数第二）→ 存疑（最后）。"""
    mode = mode or get_summary_sort(cfg)
    if mode == "default":
        return sort_items(items, "failed_first")
    if mode == "name":
        return sorted(items, key=lambda i: i.name)

    def tier(i) -> int:
        if is_fixed(cfg, i):
            return 9 if mode == "fixed_last" else 8
        if i.scanned and i.daily == "failed" and not i.excluded:
            return 0          # 未完成优先：还没做日常的排最前
        if i.has_error:
            return 1
        if i.suspicious:
            return 3 if mode == "fixed_last" else 9
        return 2

    return sorted(items, key=lambda i: (tier(i), i.name))


def get_excluded(cfg: dict) -> List[str]:
    raw = (cfg.get("check") or {}).get("excluded") or []
    return [str(x) for x in raw]


def format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


# ==================== 引擎 ====================

def _file_contains(path: str, target: str) -> bool:
    """分块读取判断文件是否含关键字，避免超大日志一次性 read() 撑爆内存。

    保留 len(target)-1 字节重叠处理跨块命中；同时兼容 GBK 等非 UTF-8 中文日志。"""
    need = target.encode("utf-8")
    if not need:
        return False
    overlap = len(need) - 1
    with open(path, "rb") as f:
        buf = b""
        while True:
            chunk = f.read(256 * 1024)
            if not chunk:
                break
            if overlap > 0:
                data = buf + chunk
                buf = chunk[-overlap:] if len(chunk) > overlap else chunk
            else:
                data = chunk
            if need in data:
                return True
        # 兜底：如果 UTF-8 字节没命中，可能是 GBK 编码，再按 GBK 试一次
        if _contains_gbk(path, target):
            return True
    return False


def _contains_gbk(path: str, target: str) -> bool:
    try:
        with open(path, "rb") as f:
            raw = f.read(2 * 1024 * 1024)   # 兜底扫描限 2MB，避免超大文件二次全读
        for enc in ("gb18030", "gbk", "utf-8-sig"):
            try:
                if target in raw.decode(enc):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


class Engine:
    """扫描 / 检查引擎。耗时操作在 worker 线程里跑，cancel 可中途停止。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.cancel = threading.Event()
        self.state: Dict = {}

    # ---------- 状态文件（与原脚本共用 D:\Automate\Cache） ----------
    def _cache_root(self) -> str:
        return os.path.normpath((self.cfg.get("check") or {}).get("cache_dir")
                                or r"D:\Automate\Cache")

    def _state_path(self) -> str:
        return os.path.join(self._cache_root(), "error_detect_state.json")

    def _log_path(self) -> str:
        return os.path.join(self._cache_root(), "DailyCheck.txt")

    def load_error_state(self) -> Dict:
        default = {"games": {}, "game_last_dates": {}, "last_run_date": None}
        try:
            with open(self._state_path(), "r", encoding="utf-8") as f:
                state = json.load(f)
            for k, v in default.items():
                state.setdefault(k, v)
            return state
        except Exception:
            return default

    def save_error_state(self, state: Dict) -> None:
        """原子写状态：写一半崩溃会导致连续无错天数/错误记录丢失。"""
        os.makedirs(self._cache_root(), exist_ok=True)
        p = self._state_path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)

    def save_daily_log(self, success: List[str], failed: List[str],
                       suspicious: Dict[str, int]) -> None:
        try:
            os.makedirs(self._cache_root(), exist_ok=True)
            now = time.localtime()
            lines = [
                "=" * 50,
                f"游戏日常检测报告 - {time.strftime('%Y年%m月%d日', now)}",
                f"检测时间: {time.strftime('%Y-%m-%d %H:%M:%S', now)}",
                "=" * 50,
                "",
                "【未完成的游戏】",
            ]
            lines += [f"  [X] {g}日常未完成！" for g in failed] if failed else ["  无"]
            lines += ["", "【已完成的游戏】"]
            lines += [f"  [OK] {g}日常已完成！" for g in success] if success else ["  无"]
            lines += ["", "【存疑】",
                      "  " + "、".join(suspicious) if suspicious else "  无",
                      "", "=" * 50]
            with open(self._log_path(), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass

    # ---------- 收集条目 ----------
    def collect(self) -> List[Game]:
        self.state = self.load_error_state()
        streaks: Dict[str, int] = self.state.get("games", {}) or {}
        excluded = set(get_excluded(self.cfg))
        games: List[Game] = []
        # 判定归属必须用「当前扫描目录」，不能用写死的 BASE_DIR，
        # 否则设置里新增的根目录下游戏拿不到存疑/日志体积/无错天数。
        scan_roots = [os.path.normpath(d).lower()
                      for d in get_scan_dirs(self.cfg) if str(d).strip()]
        for g in all_games(self.cfg):
            program_dir = program_dir_of(g.folder, self.cfg)
            pd = os.path.normpath(program_dir).lower()
            rule = rule_of(self.cfg, g.name)
            it = Game(
                key=g.name, name=g.name, folder=g.folder,
                target=rule_keyword(rule, g), extension=rule_ext(rule, g),
                success_message=g.success_message,
                program_dir=program_dir,
                is_maawork=any(pd == b or pd.startswith(b + os.sep) for b in scan_roots),
                streak=int(streaks.get(g.name, 0) or 0)
                if streak_check_enabled(self.cfg) else 0,
            )
            it.excluded = g.name in excluded
            if it.excluded:
                it.detail = "已排除，不参与检查"
            it.rule = describe_rule(it.target, it.folder, it.extension,
                                    rule.get("methods") or [DEFAULT_METHOD],
                                    rule.get("file_target", ""), True,
                                    get_log_folders(self.cfg)
                                    if log_folder_check_enabled(self.cfg) else None)
            games.append(it)
        return games

    # ---------- 单游戏扫描 ----------
    @staticmethod
    def _files_of(folder: str, ext: Optional[str], today_only: bool = False) -> List[str]:
        """列出符合条件的日志文件；today_only 只保留今天修改过的。"""
        if not os.path.exists(folder):
            return []
        names = [f for f in os.listdir(folder)
                 if os.path.isfile(os.path.join(folder, f)) and not f.startswith(".")]
        exts = parse_exts(ext)
        if exts:
            names = [f for f in names
                     if any(f.lower().endswith(e) for e in exts)]
        if today_only:
            today = time.localtime().tm_yday
            keep = []
            for f in names:
                try:
                    mt = os.path.getmtime(os.path.join(folder, f))
                    if time.localtime(mt).tm_yday == today \
                            and time.localtime(mt).tm_year == time.localtime().tm_year:
                        keep.append(f)
                except OSError:
                    continue
            names = keep
        return names

    @classmethod
    def _search_in_files(cls, folder: str, target: str, ext: Optional[str],
                         method: str = "keyword") -> Tuple[bool, int, str]:
        today_only = method in ("keyword_today", "log_today")
        if not os.path.exists(folder):
            return False, 0, f"自动化程序日志文件夹不存在：{folder}"
        # 空关键字必须拒绝："" in 任意字符串恒为真，会让「未绑定规则」的游戏只要
        # 目录里有文件就被误判成「日常完成」
        if not (target or "").strip():
            return False, 0, "未设置完成关键字"
        if method == "log_today":
            names = cls._files_of(folder, ext, today_only=True)
            return (True, len(names), "") if names else (False, 0, "")
        scanned = 0
        try:
            names = cls._files_of(folder, ext, today_only=today_only)
            for name in names:
                path = os.path.join(folder, name)
                try:
                    scanned += 1
                    if _file_contains(path, target):
                        return True, scanned, ""
                except Exception:
                    continue
            return False, scanned, ""
        except Exception as e:
            return False, scanned, str(e)

    @staticmethod
    def _log_folder_size(folder: str, ext: str = ".log") -> int:
        if not folder or not os.path.isdir(folder):
            return 0
        total = 0
        try:
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if not os.path.isfile(path):
                    continue
                if not name.lower().endswith(ext.lower()):
                    continue
                try:
                    total += os.path.getsize(path)
                except OSError:
                    continue
        except OSError:
            pass
        return total

    @classmethod
    def _match_method(cls, folder: str, ext: Optional[str], method: str,
                      keyword: str, file_target: str) -> Tuple[bool, int, str]:
        """单种判断方式的判定：返回 (是否满足, 扫描文件数, 错误)。"""
        if method in FILE_METHODS:
            if not file_target:
                return False, 0, "未设置目标文件名"
            if not os.path.isdir(folder):
                return False, 0, f"自动化程序日志文件夹不存在：{folder}"
            path = os.path.join(folder, file_target)
            if not os.path.exists(path):
                return False, 0, ""
            if method == "file_today":
                try:
                    mt = time.localtime(os.path.getmtime(path))
                    now = time.localtime()
                    if (mt.tm_yday, mt.tm_year) != (now.tm_yday, now.tm_year):
                        return False, 1, ""
                except OSError:
                    return False, 1, ""
            return True, 1, ""
        return cls._search_in_files(folder, keyword, ext, method)

    def _candidate_folders(self, it: Game, rule: dict) -> List[str]:
        """日志文件夹判定：自动化程序目录 + 它下面指定的几个日志文件夹。

        folder 现在是程序根目录；旧配置若仍指向日志子目录，它本身就是一个候选。
        只列这些目录的顶层文件，不递归子目录，也不会去翻程序里的其它文件夹。
        """
        folders = [it.folder]
        if not log_folder_check_enabled(self.cfg):
            return folders
        seen = {os.path.normcase(os.path.normpath(it.folder))}
        for name in get_log_folders(self.cfg):
            p = name if os.path.isabs(name) else os.path.join(it.program_dir, name)
            p = os.path.normpath(p)
            key = os.path.normcase(p)
            if key not in seen:
                seen.add(key)
                folders.append(p)
        return folders

    def scan_item(self, it: Game) -> None:
        """检查单个游戏：逐个尝试判断方式 × 允许的日志文件夹，任一满足即算完成。"""
        it.state = "running"
        rule = rule_of(self.cfg, it.name)
        methods = rule.get("methods") or [DEFAULT_METHOD]
        file_target = str(rule.get("file_target") or "").strip()
        folders = self._candidate_folders(it, rule)
        folder_names = (get_log_folders(self.cfg)
                        if log_folder_check_enabled(self.cfg) else None)
        it.rule = describe_rule(it.target, it.folder, it.extension, methods,
                                file_target, True, folder_names)
        done = False
        scanned = 0
        errs: List[str] = []
        for m in methods:
            for idx, folder in enumerate(folders):
                ok, n, err = self._match_method(folder, it.extension, m,
                                                it.target, file_target)
                scanned += n
                # 附加文件夹不存在是常态，只报主自动化程序目录的错误，别刷一屏
                if err and idx == 0:
                    errs.append(err)
                if ok:
                    done = True
                    break
            if done:
                break
        it.files_scanned = scanned
        it.daily = "done" if done else "failed"
        if errs and not done:
            it.detail = "；".join(errs)
        elif done:
            it.detail = it.success_message
        else:
            it.detail = describe_failure(it.target, it.extension, methods, file_target,
                                         scanned, any(os.path.isdir(f) for f in folders))
        it.log_size = self._log_folder_size(it.debug_dir) if it.is_maawork else 0
        it.threshold_mb = get_threshold_mb(self.cfg, it.name)
        it.scanned = True
        it.state = "done"

    # ---------- 异常监控（整树扫 on_error / screencap） ----------
    def scan_errors(self, games: List[Game]) -> Dict[str, Set[str]]:
        found: Dict[str, Set[str]] = {}
        if not error_check_enabled(self.cfg):
            return found
        target_set = set(get_target_folders(self.cfg))
        if not target_set:
            return found
        folder_map = get_folder_map(self.cfg)
        by_name = {g.name: g for g in games}
        excludes = get_scan_excludes(self.cfg)
        roots = [d for d in get_scan_dirs(self.cfg) if os.path.isdir(d)]
        for root in roots:
            if self.cancel.is_set():
                break
            for dirpath, dirnames, _ in os.walk(root):
                if self.cancel.is_set():
                    break
                # 被排除的子目录整棵跳过
                dirnames[:] = [d for d in dirnames
                               if not is_excluded_dir(os.path.join(dirpath, d), excludes)]
                hits = [d for d in dirnames if d in target_set]
                if not hits:
                    continue
                display = self._resolve_game_name(dirpath, folder_map, by_name)
                if not display or display in set(get_excluded(self.cfg)):
                    continue                    # 认不出是哪款游戏 / 被排除，都不写进统计
                bucket = found.setdefault(display, set())
                for t in hits:
                    bucket.add(t)
                # My-maawork 游戏条目直接标记，列表行能立刻看到
                it = by_name.get(display)
                if it is not None:
                    it.error_flags |= set(hits)
        return found

    @staticmethod
    def _resolve_game_name(dirpath: str, folder_map: dict, by_name: dict) -> str:
        """沿目录树往上找能对应到已知游戏的一层。

        异常目录可能嵌在 debug/ 之类的子目录里，只取父目录名会凭空造出
        一个叫「debug」的游戏条目，污染通知和天数统计。
        """
        cur = os.path.normpath(dirpath)
        while True:
            name = os.path.basename(cur)
            if name:
                mapped = resolve_folder_name(name, folder_map)
                if mapped in by_name:
                    return mapped
            parent = os.path.dirname(cur)
            if parent == cur:
                return ""
            cur = parent

    def update_streaks(self, all_games: List[str], error_games: Set[str],
                       daily_failed: Set[str]) -> None:
        if not streak_check_enabled(self.cfg):
            return                     # 关掉后既不清零也不累加，状态文件原样保留
        today = time.strftime("%Y-%m-%d")
        games_state: Dict[str, int] = self.state.setdefault("games", {})
        last_dates: Dict[str, str] = self.state.setdefault("game_last_dates", {})
        for game in all_games:
            if last_dates.get(game) == today:
                # 今天已计过（脚本链先跑过），但本轮失败仍要把连续天数清零
                if game in error_games or game in daily_failed:
                    games_state[game] = 0
                continue
            if game in error_games or game in daily_failed:
                games_state[game] = 0
            else:
                games_state[game] = games_state.get(game, 0) + 1
            last_dates[game] = today
        self.state["last_run_date"] = today
        self.save_error_state(self.state)

    # ---------- 完整一轮检查 ----------
    def run_check(self, games: List[Game], on_item=None, on_progress=None) -> dict:
        """按原脚本流程跑一轮：日常检测 → 异常监控 → 存疑过滤 → 落盘。

        cancel 由调用方在启动 worker 前清理；这里再 clear 会把用户刚点下的
        「停止」一起抹掉（GUI 线程 set 与 worker 启动之间存在竞态）。
        """
        total = len(games)
        failed: List[str] = []
        success: List[str] = []
        suspicious: Dict[str, int] = {}

        for i, it in enumerate(games):
            if self.cancel.is_set():
                break
            if it.excluded:
                continue                    # 排除的不扫描、不计入成败
            self.scan_item(it)
            if on_item:
                on_item(it)
            if on_progress:
                on_progress(i + 1, total, it.name)
            (success if it.daily == "done" else failed).append(it.name)
            # 存疑初筛：仅 My-maawork 下的程序参与，按各自阈值；关掉开关后完全不判
            if (log_threshold_check_enabled(self.cfg) and it.is_maawork
                    and it.log_size > it.threshold_mb * 1024 * 1024):
                suspicious[it.name] = it.log_size

        if self.cancel.is_set():
            return {"cancelled": True}

        # 异常监控：扫 on_error / screencap，并把日常失败纳入无错天数清零
        error_scan_status = "completed"
        if not error_check_enabled(self.cfg):
            error_scan_status = "disabled"
            error_found = {}
        elif not get_target_folders(self.cfg):
            error_scan_status = "disabled"      # 没有要监控的目录，等同关闭
            error_found = {}
        else:
            error_found = self.scan_errors(games)
            if self.cancel.is_set():
                # 异常扫描被取消：绝不能拿半截结果更新无错天数 / 写日报
                return {"cancelled": True}
        folder_error_games = sorted(error_found.keys())
        excluded_disp = set(get_excluded(self.cfg))
        folder_map = get_folder_map(self.cfg)
        all_games = sorted(resolve_folder_name(n, folder_map)
                           for n in self._maawork_dirs()
                           if resolve_folder_name(n, folder_map) not in excluded_disp)
        self.update_streaks(all_games, set(folder_error_games), set(failed))

        # 把最新无错天数写回条目
        streaks = self.state.get("games", {}) or {}
        for it in games:
            it.streak = int(streaks.get(it.name, it.streak) or 0)

        # 存疑剔除：已在日常失败 / 错误捕获里出现过的不再重复报
        already = set(failed) | set(folder_error_games)
        for g in [g for g in suspicious if g in already]:
            suspicious.pop(g, None)
        for it in games:
            it.suspicious = it.name in suspicious

        self.save_daily_log(success, failed, suspicious)

        return {
            "cancelled": False,
            "success": success,
            "failed": failed,
            "suspicious": suspicious,
            "folder_errors": error_found,
            "error_scan_status": error_scan_status,
        }

    def _maawork_dirs(self) -> List[str]:
        folder_map = get_folder_map(self.cfg)
        excludes = get_scan_excludes(self.cfg)
        out: List[str] = []
        for root in get_scan_dirs(self.cfg):
            try:
                for n in os.listdir(root):
                    p = os.path.join(root, n)
                    if os.path.isdir(p) and folder_keyword_hit(n, folder_map) \
                            and not is_excluded_dir(p, excludes) and n not in out:
                        out.append(n)
            except Exception:
                continue
        return out

    # ---------- 消息构建（与原脚本四条通知一致） ----------
    def sort_by_order(self, games: Iterable[str]) -> List[str]:
        order = {g.name: i for i, g in enumerate(all_games(self.cfg))}
        def key(g: str):
            return (0, order[g], g) if g in order else (1, 0, g)
        return sorted(set(games), key=key)

    def build_error_message(self, result: dict) -> str:
        error_found: Dict[str, Set[str]] = result["folder_errors"]
        games_state = self.state.get("games", {}) or {}
        folder_map = get_folder_map(self.cfg)
        all_games = sorted(resolve_folder_name(n, folder_map)
                           for n in self._maawork_dirs())
        error_games = set(error_found.keys()) | set(result["failed"])
        lines: List[str] = []
        if error_found:
            lines.append("⚠️ 发现异常：")
            for game in self.sort_by_order(error_found.keys()):
                desc = [TARGET_DESC_MAP.get(t, t) for t in sorted(error_found[game])]
                lines.append(f"  {game}（{','.join(desc)}）")
        else:
            lines.append("✅ 未发现异常")
        normal = [g for g in all_games if g not in error_games]
        if normal and streak_check_enabled(self.cfg):
            lines += ["", "无错天数："]
            for game in self.sort_by_order(normal):
                lines.append(f"  {game}：{games_state.get(game, 0)}")
        return "\n".join(lines)

    def build_suspicious_message(self, suspicious: Dict[str, int]) -> str:
        if suspicious:
            return "⚠️ 存疑：" + "、".join(self.sort_by_order(suspicious.keys()))
        return "✅ 未发现存疑"

    def build_daily_message(self, success: List[str], failed: List[str]) -> str:
        success = self.sort_by_order(success)
        failed = self.sort_by_order(failed)
        total = len(success) + len(failed)
        lines: List[str] = []
        if not failed:
            lines.append(f"✅ 所有游戏日常已完成！（共 {total} 款）")
            lines.append("【已完成】")
            lines += [f"  ✓ {g}" for g in success]
        else:
            lines.append(f"❌ 失败游戏 {len(failed)} 个：")
            lines += [f"  ✗ {g}" for g in failed]
            if success:
                lines += ["", f"✅ 成功游戏 {len(success)} 个："]
                lines += [f"  ✓ {g}" for g in success]
        return "\n".join(lines)

    def build_summary_message(self, failed: List[str],
                              folder_error_games: List[str],
                              suspicious: Dict[str, int]) -> str:
        folder_error_games = self.sort_by_order(folder_error_games)
        failed = self.sort_by_order(failed)
        lines: List[str] = []
        if folder_error_games:
            lines.append(f"⚠️ 发现异常（{len(folder_error_games)} 个）：")
            lines += [f"  ✗ {g}" for g in folder_error_games]
        else:
            lines.append("✅ 未发现异常")
        lines.append("")
        if failed:
            lines.append(f"❌ 日常未完成（{len(failed)} 个）：")
            lines += [f"  ✗ {g}" for g in failed]
        else:
            lines.append("✅ 所有游戏日常已完成")
        lines.append("")
        if suspicious:
            lines.append("⚠️ 存疑：" + "、".join(self.sort_by_order(suspicious.keys())))
        else:
            lines.append("✅ 无存疑")
        return "\n".join(lines)

    def build_all_messages(self, result: dict) -> List[Tuple[str, str]]:
        """四条钉钉通知：异常监控 / 存疑提醒 / 日常检测 / 日常汇总。"""
        return [
            ("异常监控", self.build_error_message(result)),
            ("存疑提醒", self.build_suspicious_message(result["suspicious"])),
            ("日常检测", self.build_daily_message(result["success"], result["failed"])),
            ("日常汇总", self.build_summary_message(result["failed"],
                                                    list(result["folder_errors"].keys()),
                                                    result["suspicious"])),
        ]

    # ---------- 钉钉 ----------
    @staticmethod
    def send_dingtalk(cfg: dict, content: str) -> Tuple[bool, str]:
        ding = cfg.get("dingtalk") or {}
        webhook = (ding.get("webhook") or "").strip()
        secret = (ding.get("secret") or "").strip()
        if not webhook:
            return False, "未配置 Webhook"
        if not webhook.lower().startswith("https://"):
            return False, "仅支持 https:// 的 Webhook（http 会明文泄露 access_token）"
        try:
            if secret:
                ts = str(round(time.time() * 1000))
                sign = urllib.parse.quote_plus(
                    base64.b64encode(hmac.new(secret.encode("utf-8"),
                                              f"{ts}\n{secret}".encode("utf-8"),
                                              hashlib.sha256).digest()))
                # 用 urlparse 正确合并 query：先移除旧 timestamp/sign，再追加，避免重复参数/fragment 错位
                parts = urllib.parse.urlsplit(webhook)
                q = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                q = [(k, v) for k, v in q if k not in ("timestamp", "sign")]
                q += [("timestamp", ts), ("sign", sign)]
                url = urllib.parse.urlunsplit(
                    (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(q), ""))
            else:
                url = webhook
            msg = {"msgtype": "text", "text": {"content": content}}
            req = urllib.request.Request(
                url, data=json.dumps(msg).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read(64 * 1024).decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
                ok = int(data.get("errcode", -1)) == 0
                detail = str(data.get("errmsg") or body)
            except Exception:
                ok, detail = False, body
            return ok, ("发送成功" if ok else detail)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read(64 * 1024).decode("utf-8", errors="replace")
            except Exception:
                detail = "无响应体"
            return False, f"HTTP {e.code}: {detail}"
        except Exception as e:
            return False, str(e)


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
APP_VERSION = "1.5.0"

# ==================== 监控配置（与原脚本一致） ====================

BASE_DIR = r"D:\Automate\My-maawork"
DEFAULT_SCAN_DIRS = [BASE_DIR]      # 默认扫描的自动化程序根目录，可在设置里增删
TARGET_FOLDERS = ["on_error", "screencap"]
DEBUG_FOLDER_NAME = "debug"

TARGET_DESC_MAP: Dict[str, str] = {
    "on_error": "运行出错",
    "screencap": "捕获到错误截图",
}

FOLDER_NAME_MAP: Dict[str, str] = {
    "MFAAWatcher-of-Realms": "潮汐守望者",
    "MFAAE7": "第七史诗",
    "MFAAResonance": "雷索纳斯",
    "MFAAAFK-Journey": "剑与远征启程",
    "MFAALily-Blade": "铃兰之剑",
    "MFAAFGO": "FGO",
    "MFAATDJ": "天地劫",
    "MFAADragonheir": "龙息神迹",
    "MFAAAligames": "坎公骑冠剑",
    "MFAALangrisser": "梦幻模拟战",
    "MFAAKes": "卡厄思梦境",
    "MFAAMecharashi": "钢岚",
    "MFAAXgame": "众神派对",
}

DEFAULT_DING = {
    "channel": "dingtalk",
    "enabled": True,
    "webhook": "https://oapi.dingtalk.com/robot/send?access_token=847f896ac85d9aaa98ebd04d129d670444e0ecaeb25bb25d4bb8435ecb099541",
    "secret": "SEC345cec0e4827a467b4c430c672b9e0e1d1c9506b288027dda6026419e132470b",
}


@dataclass
class GameConfig:
    name: str
    folder: str                       # 日志目录（在里面搜关键字）
    target: str                       # 成功关键字
    extension: Optional[str]
    success_message: str


DAILY_GAMES: List[GameConfig] = [
    GameConfig("原神", r"D:\Automate\BetterGI\log", '检查每日奖励结果："今日奖励已领取"', ".log", "原神日常已完成！"),
    GameConfig("星铁", r"D:\Automate\March7thAssistant\logs", "每日实训奖励完成", ".log", "星铁日常已完成！"),
    GameConfig("绝区零", r"D:\Automate\ZZZ-onedragon\.log", "指令[ 活跃度奖励 ] 执行成功 返回状态 成功", None, "绝区零日常已完成！"),
    GameConfig("崩坏三", r"D:\Automate\Maa_bbb\debug", "所有任务都已完成", ".log", "崩坏三日常已完成！"),
    GameConfig("1999", r"D:\Automate\M9A\logs", "任务已全部完成", ".log", "1999日常已完成！"),
    GameConfig("明日方舟", r"D:\Automate\MAA\debug", "任务已全部完成！", ".log", "明日方舟日常已完成！"),
    GameConfig("终末地", r"D:\Automate\MaaEnd\debug", "MXU_KILLPROC", ".log", "终末地日常已完成！"),
    GameConfig("潮汐守望者", r"D:\Automate\My-maawork\MFAAWatcher-of-Realms\logs", "任务已全部完成！", ".log", "潮汐守望者日常已完成！"),
    GameConfig("第七史诗", r"D:\Automate\My-maawork\MFAAE7\logs", "任务已全部完成！", ".log", "第七史诗日常已完成！"),
    GameConfig("钢岚", r"D:\Automate\My-maawork\MFAAMecharashi\logs", "任务已全部完成！", ".log", "钢岚日常已完成！"),
    GameConfig("雷索纳斯", r"D:\Automate\My-maawork\MFAAResonance\logs", "任务已全部完成！", ".log", "雷索纳斯日常已完成！"),
    GameConfig("剑与远征启程", r"D:\Automate\My-maawork\MFAAAFK-Journey\logs", "任务已全部完成！", ".log", "剑与远征启程日常已完成！"),
    GameConfig("铃兰之剑", r"D:\Automate\My-maawork\MFAALily-Blade\logs", "任务已全部完成！", ".log", "铃兰之剑日常已完成！"),
    GameConfig("FGO", r"D:\Automate\My-maawork\MFAAFGO\logs", "任务已全部完成！", ".log", "FGO日常已完成！"),
    GameConfig("天地劫", r"D:\Automate\My-maawork\MFAATDJ\logs", "任务已全部完成！", ".log", "天地劫日常已完成！"),
    GameConfig("龙息神迹", r"D:\Automate\My-maawork\MFAADragonheir\logs", "任务已全部完成！", ".log", "龙息神迹日常已完成！"),
    GameConfig("坎公骑冠剑", r"D:\Automate\My-maawork\MFAAAligames\logs", "任务已全部完成！", ".log", "坎公骑冠剑日常已完成！"),
    GameConfig("梦幻模拟战", r"D:\Automate\My-maawork\MFAALangrisser\logs", "任务已全部完成！", ".log", "梦幻模拟战日常已完成！"),
    GameConfig("卡厄思梦境", r"D:\Automate\My-maawork\MFAAKes\logs", "任务已全部完成！", ".log", "卡厄思梦境日常已完成！"),
    GameConfig("众神派对", r"D:\Automate\My-maawork\MFAAXgame\logs", "任务已全部完成！", ".log", "众神派对日常已完成！"),
]

DEFAULT_LOG_THRESHOLD_MB = 10
# 早期版本给 FGO / 潮汐守望者 / 钢岚 单独调过阈值（21 / 16 / 12）。现在统一成 10：
# 新设备上所有游戏都是 10；老配置里已经存下的单游戏阈值视为用户手动设置，原样保留。
DEFAULT_GAME_THRESHOLDS: Dict[str, int] = {}

# 日常完成判断方式（第 1 种与原脚本一致）
# 说明文字要写清「看哪些文件 / 比什么 / 以什么为准」，别只写「关键词匹配」
METHODS = [
    ("keyword", "关键字（不限日期）",
     "在日志目录里逐个打开 .log 读全文，内容里出现该游戏的完成关键字即算完成。"
     "不看文件日期，几天前的旧日志命中也会算。"),
    ("keyword_today", "关键字（仅今天的日志）",
     "同上，但只打开修改时间是今天的 .log 读全文，"
     "避免把昨天或前几天的成功记录当成本次完成。"),
    ("log_today", "今天有新日志",
     "不读日志内容：日志目录里只要有一个修改时间是今天的 .log 就算完成。"),
    ("file_exists", "特定文件已生成",
     "不读日志内容：目录里存在指定文件名的文件就算完成，不看日期。"
     "文件名写在规则里（「判定规则」页编辑规则时填）。"),
    ("file_today", "特定文件今天生成",
     "指定文件存在、且修改时间是今天，才算完成。文件名同样写在规则里。"),
]
METHOD_KEYS = tuple(k for k, _, _ in METHODS)
# 一句话版（列表/卡片里用，详细规则见 METHODS 的第三条与每行「匹配规则」）
METHOD_BRIEF = {
    "keyword": "在日志目录所有 *.log 全文里找该游戏的关键字，命中即完成（不看日期）",
    "keyword_today": "只读今天修改过的 *.log，命中该游戏关键字才算完成",
    "log_today": "今天有新的 *.log 就算完成（不读内容、不比关键字）",
    "file_exists": "目录里出现指定文件名就算完成（不看日期）",
    "file_today": "指定文件存在且今天修改过才算完成",
}
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
# 游戏只决定「看哪个日志目录」，规则本身可以给多款游戏共用（例：MAA 系十几个游戏
# 都是「日志里出现『任务已全部完成！』」）。改一条规则，引用它的游戏全部跟着变。

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
RULE_FIELDS = ("id", "name", "methods", "keyword", "file_target", "ext", "note")


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


def rule_keyword(rule: dict, game: Optional[GameConfig] = None) -> str:
    kw = str((rule or {}).get("keyword") or "").strip()
    return kw or (game.target if game else "")


def describe_rule(keyword: str, folder: str, ext: Optional[str],
                  methods: List[str], file_target: str = "",
                  with_folder: bool = True) -> str:
    """把判断方式翻译成一句可核对的具体规则：看哪些文件、比什么、以什么为准。

    with_folder=False 时用「日志目录」代替完整路径（界面里一行放得下）。"""
    ext_txt = f" *{ext} " if ext else "文件"      # 带扩展名时是 " *.log "，否则直接「文件」
    if with_folder:
        where, lead = f"{folder} 下", "读 "
    else:
        where, lead = "日志目录下", "读"
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
    return "；或 ".join(out)


def rule_text(cfg: dict, game: GameConfig, rule: Optional[dict] = None,
              with_folder: bool = True) -> str:
    """把「规则 + 游戏日志目录」翻译成一句可核对的话。"""
    r = rule or rule_of(cfg, game.name)
    return describe_rule(rule_keyword(r, game), game.folder, rule_ext(r, game),
                         r.get("methods") or [DEFAULT_METHOD],
                         r.get("file_target", ""), with_folder)


def rule_name_of(cfg: dict, game_name: str) -> str:
    """该游戏引用的规则名（没绑规则时给出明确说明）。"""
    rid = rule_id_for(cfg, game_name)
    r = get_rule(cfg, rid) if rid else None
    return r["name"] if r else "未绑定（沿用默认判断方式）"


def describe_failure(keyword: str, ext: Optional[str], methods: List[str],
                     file_target: str, scanned: int, folder_exists: bool) -> str:
    """未完成时的一句话原因：比了什么关键字、读了几个什么文件。"""
    ext_txt = ext or "文件"
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
        return f"{head} · 日志目录不存在"
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
SORT_MODES = SORT_MODES_BASIC + [kv for kv in SORT_MODES_SPECIAL if kv[0] != "none"]
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


def open_url(url: str) -> None:
    try:
        os.startfile(url)            # noqa: S606
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
                  "default_methods": [],
                  "error_check": True,
                  "target_folders": list(TARGET_FOLDERS),
                  "folder_map": {},
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
            else:
                cfg[k] = v
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(cache_dir(), exist_ok=True)
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


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


def get_method(cfg: dict, game_name: str) -> str:
    """兼容接口：返回该游戏第一个生效的判断方式。"""
    ms = get_methods(cfg, game_name)
    return ms[0] if ms else DEFAULT_METHOD


def get_methods(cfg: dict, game_name: str) -> List[str]:
    """该游戏生效的判断方式列表（任一满足即算完成），来自它引用的规则。"""
    return list(rule_of(cfg, game_name)["methods"])


def get_file_target(cfg: dict, game_name: str) -> str:
    """file_exists / file_today 方式要检测的文件名（相对日志目录），来自规则。"""
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


def effective_sort(cfg: dict) -> str:
    """实际生效的排序：特殊排序启用时覆盖常规排序。"""
    sp = get_sort_special(cfg)
    return sp if sp != "none" else get_sort_mode(cfg)


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
        return [ch]
    return ["dingtalk"] if ding.get("enabled", True) else []


def get_notify_channel(cfg: dict) -> str:
    """兼容接口：返回第一个启用的通知方式，没有则 "none"。"""
    chans = get_notify_channels(cfg)
    return chans[0] if chans else "none"


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
    """文件夹关键词 → 游戏显示名；用户配置叠加在默认映射之上。"""
    merged = dict(FOLDER_NAME_MAP)
    extra = (cfg.get("check") or {}).get("folder_map") or {}
    for k, v in extra.items():
        k, v = str(k).strip(), str(v).strip()
        if k and v:
            merged[k] = v
    return merged


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
        try:
            os.makedirs(self._cache_root(), exist_ok=True)
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

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
        for g in all_games(self.cfg):
            program_dir = os.path.dirname(os.path.normpath(g.folder))
            base = os.path.normpath(BASE_DIR).lower()
            pd = os.path.normpath(program_dir).lower()
            rule = rule_of(self.cfg, g.name)
            it = Game(
                key=g.name, name=g.name, folder=g.folder,
                target=rule_keyword(rule, g), extension=rule_ext(rule, g),
                success_message=g.success_message,
                program_dir=program_dir,
                is_maawork=(pd == base or pd.startswith(base + os.sep)),
                streak=int(streaks.get(g.name, 0) or 0),
            )
            it.excluded = g.name in excluded
            if it.excluded:
                it.detail = "已排除，不参与检查"
            it.rule = describe_rule(it.target, it.folder, it.extension,
                                    rule.get("methods") or [DEFAULT_METHOD],
                                    rule.get("file_target", ""))
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
        if ext:
            names = [f for f in names if f.lower().endswith(ext.lower())]
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
            return False, 0, f"日志目录不存在：{folder}"
        if method == "log_today":
            names = cls._files_of(folder, ext, today_only=True)
            return (True, len(names), "") if names else (False, 0, "")
        scanned = 0
        try:
            names = cls._files_of(folder, ext, today_only=today_only)
            for name in names:
                path = os.path.join(folder, name)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        scanned += 1
                        if target in fh.read():
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
                return False, 0, f"日志目录不存在：{folder}"
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

    def scan_item(self, it: Game) -> None:
        """检查单个游戏：逐个尝试判断方式，任一满足即算完成。"""
        it.state = "running"
        methods = get_methods(self.cfg, it.name)
        file_target = get_file_target(self.cfg, it.name)
        it.rule = describe_rule(it.target, it.folder, it.extension, methods, file_target)
        done = False
        scanned = 0
        errs: List[str] = []
        for m in methods:
            ok, n, err = self._match_method(it.folder, it.extension, m,
                                            it.target, file_target)
            scanned += n
            if err:
                errs.append(err)
            if ok:
                done = True
                break
        it.files_scanned = scanned
        it.daily = "done" if done else "failed"
        if errs and not done:
            it.detail = "；".join(errs)
        elif done:
            it.detail = it.success_message
        else:
            it.detail = describe_failure(it.target, it.extension, methods, file_target,
                                         scanned, os.path.isdir(it.folder))
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
                display = folder_map.get(os.path.basename(os.path.dirname(dirpath)),
                                         os.path.basename(os.path.dirname(dirpath)))
                if display in set(get_excluded(self.cfg)):
                    continue                    # 被排除的游戏不参与异常监控
                bucket = found.setdefault(display, set())
                for t in hits:
                    bucket.add(t)
                # My-maawork 游戏条目直接标记，列表行能立刻看到
                it = by_name.get(display)
                if it is not None:
                    it.error_flags |= set(hits)
        return found

    def update_streaks(self, all_games: List[str], error_games: Set[str],
                       daily_failed: Set[str]) -> None:
        today = time.strftime("%Y-%m-%d")
        games_state: Dict[str, int] = self.state.setdefault("games", {})
        last_dates: Dict[str, str] = self.state.setdefault("game_last_dates", {})
        for game in all_games:
            if last_dates.get(game) == today:
                continue                      # 今天已经计过（脚本链先跑过）
            if game in error_games or game in daily_failed:
                games_state[game] = 0
            else:
                games_state[game] = games_state.get(game, 0) + 1
            last_dates[game] = today
        self.state["last_run_date"] = today
        self.save_error_state(self.state)

    # ---------- 完整一轮检查 ----------
    def run_check(self, games: List[Game], on_item=None, on_progress=None) -> dict:
        """按原脚本流程跑一轮：日常检测 → 异常监控 → 存疑过滤 → 落盘。"""
        self.cancel.clear()
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
            # 存疑初筛：仅 My-maawork 下的程序参与，按各自阈值
            if it.is_maawork and it.log_size > it.threshold_mb * 1024 * 1024:
                suspicious[it.name] = it.log_size

        if self.cancel.is_set():
            return {"cancelled": True}

        # 异常监控：扫 on_error / screencap，并把日常失败纳入无错天数清零
        if error_check_enabled(self.cfg) and get_target_folders(self.cfg):
            error_found = self.scan_errors(games)
        else:
            error_found = {}                # 用户关闭了文件夹异常检测
        folder_error_games = sorted(error_found.keys())
        excluded_disp = set(get_excluded(self.cfg))
        folder_map = get_folder_map(self.cfg)
        all_games = sorted(folder_map.get(n, n) for n in self._maawork_dirs()
                           if folder_map.get(n, n) not in excluded_disp)
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
            "error_games": sorted(set(folder_error_games) | set(failed) & set(all_games)),
        }

    def _maawork_dirs(self) -> List[str]:
        names = set(get_folder_map(self.cfg).keys())
        excludes = get_scan_excludes(self.cfg)
        out: List[str] = []
        for root in get_scan_dirs(self.cfg):
            try:
                for n in os.listdir(root):
                    p = os.path.join(root, n)
                    if os.path.isdir(p) and n in names \
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
        all_games = sorted(folder_map.get(n, n) for n in self._maawork_dirs())
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
        if normal:
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
        try:
            if secret:
                ts = str(round(time.time() * 1000))
                sign = urllib.parse.quote_plus(
                    base64.b64encode(hmac.new(secret.encode("utf-8"),
                                              f"{ts}\n{secret}".encode("utf-8"),
                                              hashlib.sha256).digest()))
                url = f"{webhook}&timestamp={ts}&sign={sign}"
            else:
                url = webhook
            msg = {"msgtype": "text", "text": {"content": content}}
            req = urllib.request.Request(
                url, data=json.dumps(msg).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            resp = urllib.request.urlopen(req, timeout=10)
            body = resp.read().decode("utf-8")
            ok = '"errcode":0' in body
            return ok, ("发送成功" if ok else body)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8")
            except Exception:
                detail = "无响应体"
            return False, f"HTTP {e.code}: {detail}"
        except Exception as e:
            return False, str(e)

    def send_dingtalk_raw(self, content: str) -> bool:
        ok, _ = self.send_dingtalk(self.cfg, content)
        return ok

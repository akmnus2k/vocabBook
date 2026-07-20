# -*- coding: utf-8 -*-
"""存储层：单词本 + 搜索历史 + 间隔重复复习计划

配置了 Google Sheets（.streamlit/secrets.toml）就存云端表格——
手机、电脑、云端网页共用同一份数据；没配置就退回本地 JSON 文件。
两种模式对外接口完全一样，app.py 不需要关心数据存在哪。
"""
import json
import os
from datetime import date, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOOK_FILE = os.path.join(BASE_DIR, "vocab_book.json")
HIST_FILE = os.path.join(BASE_DIR, "search_history.json")

# 间隔重复：等级 0~6，对应下次复习间隔（天）
# 每答对一次等级 +1，间隔拉长；答错回到等级 0，当天重新复习
INTERVALS = [0, 1, 2, 4, 7, 15, 30]

_HEADER = ["word", "data"]

_sheet = None          # 缓存的表格连接（进程内只连一次）
_sheet_failed = False  # 没配置或连不上时记下来，之后直接走本地文件


def _get_sheet():
    """配置了 Google Sheets 就返回表格对象，否则返回 None（用本地文件）"""
    global _sheet, _sheet_failed
    if _sheet is not None or _sheet_failed:
        return _sheet
    try:
        import streamlit as st
        if "gcp_service_account" not in st.secrets:
            _sheet_failed = True
            return None
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        _sheet = gspread.authorize(creds).open_by_key(st.secrets["sheet_id"])
    except Exception:
        _sheet_failed = True
    return _sheet


def _ws(name):
    """取工作表，第一次用时自动创建并写好表头"""
    sh = _get_sheet()
    try:
        return sh.worksheet(name)
    except Exception:
        ws = sh.add_worksheet(title=name, rows=2000, cols=len(_HEADER))
        ws.update([_HEADER], "A1")
        return ws


def _load_dict(ws_name, local_file):
    """读取整份数据：{单词: 词条dict}"""
    if _get_sheet() is None:
        if not os.path.exists(local_file):
            return {}
        try:
            with open(local_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    data = {}
    for row in _ws(ws_name).get_all_values()[1:]:
        if len(row) >= 2 and row[1].strip():
            try:
                data[row[0]] = json.loads(row[1])
            except json.JSONDecodeError:
                pass
    return data


def _save_dict(data, ws_name, local_file):
    """写回整份数据（数据量在几千词以内，整表重写最简单可靠）"""
    if _get_sheet() is None:
        with open(local_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return
    ws = _ws(ws_name)
    rows = [_HEADER] + [[k, json.dumps(v, ensure_ascii=False)] for k, v in data.items()]
    ws.clear()
    ws.update(rows, "A1")


# ============ 单词本 ============

def load_book() -> dict:
    return _load_dict("vocab_words", BOOK_FILE)


def save_book(book: dict):
    _save_dict(book, "vocab_words", BOOK_FILE)


def add_word(book: dict, info: dict, images: list):
    """把查到的单词收藏进单词本"""
    today = date.today().isoformat()
    book[info["word"]] = {
        "word": info["word"],
        "phone_us": info.get("phone_us", ""),
        "phone_uk": info.get("phone_uk", ""),
        "defs": info.get("defs", []),
        "en_defs": info.get("en_defs", []),
        "examples": info.get("examples", []),
        "images": images,
        "added": today,
        "level": 0,
        "next_review": today,
    }
    save_book(book)


def remove_word(book: dict, word: str):
    if word in book:
        del book[word]
        save_book(book)


def due_words(book: dict) -> list:
    """今天需要复习的词条列表"""
    today = date.today().isoformat()
    return [e for e in book.values() if e.get("next_review", today) <= today]


def grade(book: dict, word: str, known: bool):
    """复习打分：认识 -> 等级+1 间隔拉长；不认识 -> 回到等级 0"""
    entry = book.get(word)
    if not entry:
        return
    if known:
        entry["level"] = min(entry.get("level", 0) + 1, len(INTERVALS) - 1)
    else:
        entry["level"] = 0
    days = INTERVALS[entry["level"]]
    entry["next_review"] = (date.today() + timedelta(days=days)).isoformat()
    save_book(book)


# ============ 搜索历史 ============

def load_history() -> dict:
    return _load_dict("search_history", HIST_FILE)


def record_history(history: dict, word: str, brief: str):
    """记一笔搜索历史（同一个词多次查会累计次数）"""
    today = date.today().isoformat()
    e = history.get(word, {"word": word, "count": 0, "first": today})
    e["count"] += 1
    e["last"] = today
    if brief:
        e["brief"] = brief
    history[word] = e
    _save_dict(history, "search_history", HIST_FILE)


def remove_history(history: dict, word: str):
    if word in history:
        del history[word]
        _save_dict(history, "search_history", HIST_FILE)

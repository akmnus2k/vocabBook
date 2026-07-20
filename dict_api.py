# -*- coding: utf-8 -*-
"""词典接口层：联想、查词、图片、发音（全部免费接口，无需注册）"""
import re
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}


def suggest(prefix: str):
    """输入前几个字母，返回联想候选列表 [(显示文字, 单词), ...]"""
    if not prefix or len(prefix.strip()) < 1:
        return []
    try:
        r = requests.get(
            "https://dict.youdao.com/suggest",
            params={"q": prefix.strip(), "num": 8, "doctype": "json"},
            headers=HEADERS, timeout=5,
        )
        entries = r.json().get("data", {}).get("entries", [])
        results = []
        for e in entries:
            word = e.get("entry", "")
            explain = e.get("explain", "")
            label = f"{word} — {explain}" if explain else word
            # 下拉里显示"单词 — 释义"，选中后只返回单词本身
            results.append((label, word))
        return results
    except Exception:
        return []


def lookup(word: str) -> dict:
    """查询单词详情：音标、中文释义、双语例句"""
    info = {
        "word": word, "phone_us": "", "phone_uk": "",
        "defs": [], "examples": [], "found": False,
    }
    try:
        r = requests.get(
            "https://dict.youdao.com/jsonapi",
            params={"q": word}, headers=HEADERS, timeout=8,
        )
        d = r.json()
    except Exception:
        return info

    # 基础词典（英汉）
    w = (d.get("ec", {}).get("word") or [{}])[0]
    info["phone_us"] = w.get("usphone", "")
    info["phone_uk"] = w.get("ukphone", "")
    for tr in w.get("trs", []):
        try:
            info["defs"].append(tr["tr"][0]["l"]["i"][0])
        except (KeyError, IndexError, TypeError):
            pass

    # 查不到词典释义时，退回网络释义 / 机器翻译
    if not info["defs"]:
        for t in d.get("web_trans", {}).get("web-translation", [])[:1]:
            for tr in t.get("trans", [])[:3]:
                v = tr.get("value")
                if v:
                    info["defs"].append(v)
    if not info["defs"]:
        tran = d.get("fanyi", {}).get("tran")
        if tran:
            info["defs"].append(tran)

    # 双语例句（应用场景）
    for s in d.get("blng_sents_part", {}).get("sentence-pair", [])[:5]:
        en = s.get("sentence", "")
        zh = s.get("sentence-translation", "")
        if en and zh:
            info["examples"].append({"en": en, "zh": zh})

    info["found"] = bool(info["defs"])
    return info


def get_images(word: str, n: int = 3):
    """从必应图片搜索抓取前几张相关图片的地址"""
    try:
        r = requests.get(
            "https://cn.bing.com/images/search",
            params={"q": word, "first": 1},
            headers=HEADERS, timeout=10,
        )
        urls = re.findall(r'murl&quot;:&quot;(.*?)&quot;', r.text)
        if not urls:
            urls = re.findall(r'"murl":"(.*?)"', r.text)
        return urls[:n]
    except Exception:
        return []


def audio_url(word: str, accent: str = "us") -> str:
    """单词发音音频地址（type=2 美音，type=1 英音）"""
    t = 2 if accent == "us" else 1
    return f"https://dict.youdao.com/dictvoice?audio={word}&type={t}"

# -*- coding: utf-8 -*-
"""词典接口层：联想、查词、图片、发音（全部免费接口，无需注册）"""
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
        "defs": [], "en_defs": [], "examples": [], "found": False,
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

    # 英英释义（WordNet 词典，用英文解释单词）
    for tr_group in d.get("ee", {}).get("word", {}).get("trs", []):
        pos = tr_group.get("pos", "")
        for t in tr_group.get("tr", []):
            i = t.get("l", {}).get("i", "")
            if isinstance(i, list):  # 个别词条 i 是列表
                i = i[0] if i else ""
            if i:
                info["en_defs"].append(f"{pos} {i}".strip())

    # 双语例句（应用场景）
    for s in d.get("blng_sents_part", {}).get("sentence-pair", [])[:5]:
        en = s.get("sentence", "")
        zh = s.get("sentence-translation", "")
        if en and zh:
            info["examples"].append({"en": en, "zh": zh})

    info["found"] = bool(info["defs"])
    return info


PT_CONTEXTS = ["physical therapy", "rehabilitation", "patient", "clinical"]


def pt_sentences(word: str, limit: int = 5):
    """搜索单词在 PT/康复语境下的双语例句（用 "单词+场景词" 去搜例句库）"""
    seen, results = set(), []
    for ctx in PT_CONTEXTS:
        try:
            r = requests.get(
                "https://dict.youdao.com/jsonapi",
                params={"q": f"{word} {ctx}"}, headers=HEADERS, timeout=8,
            )
            pairs = r.json().get("blng_sents_part", {}).get("sentence-pair", [])
        except Exception:
            continue
        for s in pairs:
            en = s.get("sentence", "")
            zh = s.get("sentence-translation", "")
            # 只要真正包含目标单词的句子，去重后收集
            if en and zh and en not in seen and word.lower() in en.lower():
                seen.add(en)
                results.append({"en": en, "zh": zh})
                if len(results) >= limit:
                    return results
    return results


def get_images(word: str, n: int = 3, context: str = "", first: int = 1):
    """从 360 图片搜索取相关图片（返回的 thumb 在 360 自家 CDN 上，加载稳定）

    context: 附加的搜索词（比如中文释义），能大幅提高相关性——
             搜 "cane 手杖" 比只搜 "cane"（会出甘蔗、拐杖糖）准得多
    first:   翻页起点，配合"换一批"按钮用
    """
    try:
        r = requests.get(
            "https://image.so.com/j",
            params={"q": f"{word} {context}".strip(), "sn": first - 1, "pn": n + 5},
            headers={**HEADERS, "Referer": "https://image.so.com/"},
            timeout=8,
        )
        urls = [i.get("thumb") or i.get("img") for i in r.json().get("list", [])]
        return [u for u in urls if u][:n]
    except Exception:
        return []


def audio_url(word: str, accent: str = "uk") -> str:
    """单词发音音频地址（type=1 英音，type=2 美音）

    默认英音——用户在澳洲，澳式英语更接近英式发音
    """
    t = 2 if accent == "us" else 1
    return f"https://dict.youdao.com/dictvoice?audio={word}&type={t}"

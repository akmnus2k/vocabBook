# -*- coding: utf-8 -*-
"""PT 单词本：查词（联想）→ 收藏 → 复习 → 场景练习，附搜索历史"""
import html as html_lib
import random
import re
import threading
from datetime import date

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from streamlit_searchbox import st_searchbox

import ai_dialogue
import dict_api
import storage

st.set_page_config(page_title="PT 单词本", page_icon="📗", layout="centered")

# 手机上 Streamlit 默认把并排的列堆成竖排，这里强制保持横排（否则按钮各占一行太浪费空间）
# 同时压缩页面顶部的大片留白
st.markdown("""
<style>
@media (max-width: 640px) {
    div[data-testid="stHorizontalBlock"] {
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        gap: 0.5rem !important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
        min-width: 0 !important;
    }
}
div[data-testid="stMainBlockContainer"], .block-container {
    padding-top: 2.2rem !important;
}
</style>
""", unsafe_allow_html=True)


# ============ 访问密码（在 secrets.toml 里配 app_password，不配就不要密码） ============
def check_password() -> bool:
    try:
        pw = st.secrets.get("app_password", "")
    except Exception:
        pw = ""
    if not pw:
        return True
    if st.session_state.get("auth_ok"):
        return True
    st.title("📗 PT 单词本")
    entered = st.text_input("输入访问密码", type="password")
    if entered == pw:
        st.session_state.auth_ok = True
        st.rerun()
    elif entered:
        st.warning("密码不对哦，再试试？")
    return False


if not check_password():
    st.stop()


# 接口结果缓存一小时，避免每次界面刷新都重新请求
@st.cache_data(ttl=3600, show_spinner=False)
def cached_lookup(word):
    return dict_api.lookup(word)


# 医学/康复相关的关键词——图片搜索优先挑含这些词的义项（一词多义时选 PT 那个意思）
MED_KEYWORDS = [
    "拐杖", "康复", "治疗", "理疗", "临床", "步态", "假肢", "矫形", "护理",
    "医", "病", "症", "炎", "骨", "肌", "腱", "韧带", "关节", "神经",
    "脊", "椎", "瘫", "患", "肺", "心脏", "血", "脑",
    "粘连", "痉挛", "水肿", "萎缩", "劳损", "脱位", "侧弯",
]


# 有道释义里的医学类标签，如 [医] [外科] [解剖]
MED_TAG_RE = re.compile(r"\[(医|外科|解剖|生理|病理|药|内科|临床|口腔)[^\]]*\]")


def _is_med(text):
    """判断一段释义是否跟医学/康复相关"""
    return bool(MED_TAG_RE.search(text)) or any(k in text for k in MED_KEYWORDS)


def simplify_def(d, max_segs=3):
    """精简一条释义：医学义项排前面，只保留最常用的几个义项，去掉标签"""
    pos_m = re.match(r"^\s*([a-z]+\.)", d)
    pos = pos_m.group(1) + " " if pos_m else ""
    body = re.sub(r"^\s*[a-z]+\.\s*", "", d)
    body = re.sub(r"\[.*?\]", "", body)
    segs = [s.strip() for s in re.split(r"[；;]", body) if s.strip()]
    med = [s for s in segs if _is_med(s)]
    rest = [s for s in segs if s not in med]
    picked = (med + rest)[:max_segs]
    return (pos + "；".join(picked)) if picked else d


def concise_defs(defs, n=2):
    """整体精简：医学相关的整条释义排前面，最多取 n 条，逐条精简"""
    ordered = sorted(defs, key=lambda d: 0 if _is_med(d) else 1)
    return [simplify_def(d) for d in ordered[:n]]


def recommend_scenes(src):
    """按单词类型推荐相关场景（器械/动作类/其它），避免给抽象词推"教用拐杖"这种"""
    t = "".join(src.get("defs", []))
    if any(k in t for k in ["杖", "器", "仪", "支具", "轮椅", "矫形", "护具"]):
        return ["教怎么使用", "复诊看进展", "初次评估"]
    if any(k in t for k in ["肌", "关节", "韧带", "腱", "伸", "屈", "拉",
                            "活动", "运动", "力量", "步态"]):
        return ["教做动作", "初次评估", "复诊看进展"]
    # 病症、诊断、抽象概念等：解释类场景最通用
    return ["向病人解释", "初次评估", "复诊看进展"]


def img_context(info):
    """从中文释义里挑一小段当图片搜索的附加词，帮助搜索引擎消歧义

    比如 cane 的释义是 "茎；藤条；笞杖；拐杖"——优先选医学相关的"拐杖"，
    这样搜出来的是助行手杖而不是甘蔗
    """
    segs = []
    for d in info.get("defs", []):
        d = re.sub(r"\[.*?\]", "", d)  # 去掉 [外科] 这类标签
        for s in re.split(r"[；;，,、（()）]", d):
            s = re.sub(r"^[a-z]+\.\s*", "", s.strip())  # 去掉词性 n. v. adj.
            if s:
                segs.append(s)
    for s in segs:
        if any(k in s for k in MED_KEYWORDS):
            return s
    return segs[0] if segs else ""


@st.cache_data(ttl=3600, show_spinner=False)
def cached_images(word, context="", first=1):
    return dict_api.get_images(word, n=3, context=context, first=first)


# PT 场景例句一天内不会变，缓存久一点
@st.cache_data(ttl=86400, show_spinner=False)
def cached_pt_sentences(word):
    return dict_api.pt_sentences(word)


# ============ AI 诊室对话 ============
# 对话存进单词本词条（dialogues 字段），练习时直接读，秒开；
# 收藏时就后台生成好，通常一次都不用等。dialogues_ver 记生成时的提示词版本，
# 升级提示词后版本不匹配会自动重新生成。
# 内存缓存兜底：同一会话里现场生成过的词，存回词条前不重复调用
@st.cache_data(ttl=7 * 86400, show_spinner=False)
def cached_ai_dialogues(word, zh, api_key, prompt_ver):
    return ai_dialogue.generate_dialogues(word, zh, api_key)


# 自定义场景对话按（词+场景）缓存，同样的场景不重复生成
# ver 显式传入才进缓存键——升级 SCENE_PROMPT 后旧缓存才会失效
@st.cache_data(ttl=7 * 86400, show_spinner=False)
def cached_scene(word, zh, scene, api_key, ver):
    return ai_dialogue.generate_scene(word, zh, scene, api_key)


def pregen_dialogues(word, zh, api_key):
    """后台生成诊室对话并存进词条（收藏后台调用，不阻塞界面）"""
    d = ai_dialogue.generate_dialogues(word, zh, api_key)
    if not d:
        return
    # 重新读最新单词本再写回，避免覆盖其他改动
    latest = storage.load_book()
    if word in latest:
        latest[word]["dialogues"] = d
        latest[word]["dialogues_ver"] = ai_dialogue.PROMPT_VERSION
        storage.save_book(latest)


def start_pregen(word, zh, api_key):
    if api_key:
        threading.Thread(target=pregen_dialogues,
                         args=(word, zh, api_key), daemon=True).start()


def get_dialogues(word, zh, entry, api_key):
    """拿一个词的诊室对话：已存好的秒回，否则现场生成（并存回已收藏的词）"""
    ver = ai_dialogue.PROMPT_VERSION
    if entry and entry.get("dialogues") and entry.get("dialogues_ver") == ver:
        return entry["dialogues"]
    if not api_key:
        return None
    d = cached_ai_dialogues(word, zh, api_key, ver)
    if d and entry is not None:  # 已收藏的词顺手存回，下次秒开
        entry["dialogues"] = d
        entry["dialogues_ver"] = ver
        storage.save_book(book)
    return d


def sweep_missing_dialogues(words_zh, api_key, ver):
    """后台顺序补齐所有缺对话/版本过期的词，一次一个避免触发限流"""
    for word, zh in words_zh:
        latest = storage.load_book()
        e = latest.get(word)
        if not e or (e.get("dialogues") and e.get("dialogues_ver") == ver):
            continue
        d = ai_dialogue.generate_dialogues(word, zh, api_key)
        if d:
            e["dialogues"] = d
            e["dialogues_ver"] = ver
            storage.save_book(latest)


def start_sweep(book, api_key):
    """整个会话只扫一次，把存量单词的对话在后台补齐"""
    if not api_key or st.session_state.get("swept"):
        return
    ver = ai_dialogue.PROMPT_VERSION
    todo = [(w, img_context(e) or w) for w, e in book.items()
            if not (e.get("dialogues") and e.get("dialogues_ver") == ver)]
    if todo:
        st.session_state.swept = True
        threading.Thread(target=sweep_missing_dialogues,
                         args=(todo, api_key, ver), daemon=True).start()


def get_zhipu_key():
    try:
        return st.secrets.get("zhipu_api_key", "")
    except Exception:
        return ""


# 论文/学术句的标志词，含这些的例句一律不要
_ACADEMIC_MARKERS = [
    "methods", "objective", "conclusion", "results", "study", "analyzed",
    "mechanism", "explore", "investigate", "clinical feature", "cases of",
    "were collected", "in order to", "this paper", "evaluated", "revealed",
]


def pick_simple_sents(sents, n=3, max_words=14):
    """从语料例句里挑短、口语的句子；太长、太学术或重复的直接丢弃"""
    good = []
    seen = set()
    for ex in sents:
        en = ex["en"].strip()
        low = en.lower()
        # 归一化后去重：忽略大小写、标点和多余空格，避免出现两句几乎一样的
        norm = re.sub(r"[^a-z]", "", low)
        if norm in seen:
            continue
        if len(en.split()) > max_words:
            continue
        if any(m in low for m in _ACADEMIC_MARKERS):
            continue
        seen.add(norm)
        good.append(ex)
    # 含 you/your（对话感强）的排前面，再按短句优先
    good.sort(key=lambda ex: (0 if "you" in ex["en"].lower() else 1,
                              len(ex["en"].split())))
    return good[:n]


def display_count(sents):
    """句子都短就多显示一句（最多 3），有长句就少显示，避免一屏读不完"""
    if not sents:
        return 0
    short = [ex for ex in sents if len(ex["en"].split()) <= 8]
    return 3 if len(short) >= 3 else min(len(sents), 2)


def clickable_word(word, sub="", size=26, autoplay=False):
    """可点击的单词：点单词本身就发音（浏览器本地播放，零延迟）

    autoplay=True 时渲染后自动朗读一遍（浏览器拦截自动播放时静默失败，点词即可）
    """
    uk_url = dict_api.audio_url(word, "uk")
    us_url = dict_api.audio_url(word, "us")
    # 预加载一个 audio 元素：点击时直接播它，避免"现创建 Audio→异步加载→手势过期被拒"
    # （这是手机上点击没声音的根因）。aid 用词做唯一 id，避免多个单词互相干扰。
    # onerror：英音库缺这个词的录音时，自动换成美音，保证有声音。
    aid = "a_" + re.sub(r"[^a-zA-Z]", "", word)
    auto = f"<script>document.getElementById('{aid}').play().catch(function(){{}});</script>" if autoplay else ""
    sub_html = (f'<span style="font-size:14px;color:#7A8B96;margin-left:10px">'
                f'{html_lib.escape(sub)}</span>') if sub else ""
    # 单词加一条浅蓝虚线下划线，暗示"可以点"，不用再写"点我发音"
    components.html(
        f"""<audio id="{aid}" src="{uk_url}" preload="auto"
              onerror="this.onerror=null;this.src='{us_url}';"></audio>
            <div onclick="var a=document.getElementById('{aid}');a.currentTime=0;a.play().catch(function(){{}});"
              title="点击发音"
              style="cursor:pointer;font-family:'Source Sans Pro',sans-serif;
                     color:#3D4F5C;white-space:nowrap;overflow:hidden;
                     text-overflow:ellipsis;line-height:1.5">
              <span style="font-size:{size}px;font-weight:700;
                     border-bottom:2px dashed #A8D4EA;padding-bottom:1px">
                     {html_lib.escape(word)}</span>
              {sub_html}</div>{auto}""",
        height=int(size * 1.8),
    )


def refocus_searchbox():
    """把光标放回搜索框，方便连续查词"""
    components.html(
        """<script>
        const doc = window.parent.document;
        const fr = doc.querySelector('iframe[title="streamlit_searchbox.searchbox"]');
        if (fr && fr.contentDocument) {
            const inp = fr.contentDocument.querySelector('input');
            if (inp) inp.focus({preventScroll: true});
        }
        </script>""",
        height=0,
    )


def cloze(sentence, word):
    """把句子里的目标单词挖成空（连带复数等变形一起挖）"""
    return re.sub(rf"\b{re.escape(word)}\w*", "＿＿＿＿", sentence, flags=re.I)


def highlight(sentence, word):
    """把句子里的目标单词加粗"""
    return re.sub(rf"\b{re.escape(word)}\w*", lambda m: f"**{m.group(0)}**",
                  sentence, flags=re.I)


# 单词本和搜索历史整个会话只从存储读一次，之后在内存里改、随手写回存储
if "book" not in st.session_state:
    st.session_state.book = storage.load_book()
if "history" not in st.session_state:
    st.session_state.history = storage.load_history()
book = st.session_state.book
history = st.session_state.history

# 会话开始时后台补齐存量单词的诊室对话，让练习尽量都秒开
start_sweep(book, get_zhipu_key())


@st.dialog("词条详情")
def word_detail_dialog():
    """单词本里点 ⋮ 弹出的内页，左右按钮切换上一个/下一个单词"""
    words = st.session_state.get("dlg_words", [])
    idx = max(0, min(st.session_state.get("dlg_idx", 0), len(words) - 1))
    e = book.get(words[idx]) if words else None
    if e is None:
        st.info("这个词已经不在单词本里了")
        return

    # 左右切换
    nav_l, nav_mid, nav_r = st.columns([1, 2, 1])
    if nav_l.button("⬅️", disabled=(idx == 0), use_container_width=True):
        st.session_state.dlg_idx = idx - 1
        st.session_state.dlg_open = True
        st.rerun()
    nav_mid.markdown(
        f"<div style='text-align:center;color:#7A8B96;padding-top:6px'>"
        f"{idx + 1} / {len(words)}</div>", unsafe_allow_html=True)
    if nav_r.button("➡️", disabled=(idx == len(words) - 1), use_container_width=True):
        st.session_state.dlg_idx = idx + 1
        st.session_state.dlg_open = True
        st.rerun()

    clickable_word(e["word"], size=24)
    if e.get("phone_us"):
        st.caption(f"美 /{e['phone_us']}/")
    for d in concise_defs(e["defs"]):
        st.markdown(f"- {d}")
    if e.get("en_defs"):
        st.markdown(f"🗣️ *{e['en_defs'][0]}*")
    for ex in e.get("examples", [])[:1]:
        st.markdown(f"**{ex['en']}**")
        st.caption(ex["zh"])
    stars = "🌟" * e["level"] + "☆" * (len(storage.INTERVALS) - 1 - e["level"])
    st.caption(f"熟练度 {stars}　|　收藏于 {e['added']}　|　下次复习 {e['next_review']}")
    if st.button("🗑️ 从单词本移除", use_container_width=True):
        storage.remove_word(book, e["word"])
        st.rerun()


# 弹窗采用"一次性标记"：内页里点了左右切换会重新打开；点 X 关闭就真的关掉
if st.session_state.pop("dlg_open", False):
    word_detail_dialog()

st.markdown("##### 📗 PT 单词本")
tab_search, tab_book, tab_review, tab_practice = st.tabs(
    ["🔍 查单词", "📒 我的单词本", "🌱 复习", "🎯 场景练习"])


# ============ 查单词 ============
with tab_search:
    selected = st_searchbox(
        dict_api.suggest,
        key="word_search",
        placeholder="",
        clear_on_submit=True,  # 选完自动清空，直接输下一个词
        style_overrides={
            "searchbox": {
                "control": {"minHeight": 48, "fontSize": 17,
                            "borderRadius": 10},
                "option": {"fontSize": 15},
            },
        },
    )
    # 搜索框有新选择时，切换到这个词（历史记录里点词也会切换，见下面）
    if selected and selected != st.session_state.get("prev_search"):
        st.session_state.prev_search = selected
        # 统一转小写，避免 Anesthesia / anesthesia 记成两个词（全大写缩写词除外）
        st.session_state.view_word = selected if selected.isupper() else selected.lower()
        st.session_state.refocus = True  # 查完把光标放回搜索框

    target = st.session_state.get("view_word")

    if target:
        with st.spinner("查询中..."):
            info = cached_lookup(target)

        if not info["found"]:
            st.warning(f"没有查到「{target}」，检查一下拼写？")
        else:
            # 自动记入搜索历史（后台写入，不拖慢界面；同一个词一次会话只记一笔）
            if st.session_state.get("hist_recorded") != target:
                brief = simplify_def(info["defs"][0], 2) if info["defs"] else ""
                threading.Thread(
                    target=storage.record_history,
                    args=(history, info["word"], brief), daemon=True,
                ).start()
                st.session_state.hist_recorded = target

            # 单词标题：点击单词本身就发音
            clickable_word(info["word"], size=28)

            # 音标
            phones = []
            if info["phone_us"]:
                phones.append(f"美 /{info['phone_us']}/")
            if info["phone_uk"]:
                phones.append(f"英 /{info['phone_uk']}/")
            if phones:
                st.caption("　".join(phones))

            # 收藏按钮放最上面，手机上一眼就能点到
            if info["word"] in book:
                if st.button("💚 已收藏（点击移除）", use_container_width=True):
                    storage.remove_word(book, info["word"])
                    st.rerun()
            else:
                if st.button("⭐ 收藏到单词本", type="primary",
                             use_container_width=True):
                    imgs_for_save = cached_images(
                        info["word"], img_context(info),
                        st.session_state.get(f"img_first_{target}", 1))
                    storage.add_word(book, info, imgs_for_save)
                    # 收藏的同时后台把诊室对话先生成好，之后练习秒开
                    start_pregen(info["word"], img_context(info) or info["word"],
                                 get_zhipu_key())
                    st.toast(f"「{info['word']}」已收藏！", icon="⭐")
                    st.rerun()

            # 释义走"极简"路线：医学义项优先、最多两条，其余收进折叠区
            st.markdown("#### 释义")
            for d in concise_defs(info["defs"]):
                st.markdown(f"- {d}")

            # 英文释义只显示一条（上班时用英文解释就靠它）
            if info.get("en_defs"):
                st.markdown(f"🗣️ *{info['en_defs'][0]}*")

            # 应用例句：优先诊室对话（口语、贴近工作），生成不出来才退回有道例句
            st.markdown("#### 例句 · 应用场景")
            entry_in_book = book.get(info["word"])
            with st.spinner("加载例句..."):
                clinic = get_dialogues(
                    info["word"], img_context(info) or info["word"],
                    entry_in_book, get_zhipu_key())
            if clinic:
                for ex in clinic[:2]:
                    st.markdown(f"**{ex['en']}**")
                    st.caption(ex["zh"])
            elif info["examples"]:
                ex = info["examples"][0]
                st.markdown(f"**{ex['en']}**")
                st.caption(ex["zh"])
            else:
                st.caption("这个词暂时没有合适的例句")

            # 完整内容想看再展开
            has_more = (len(info["defs"]) > 2 or len(info.get("en_defs", [])) > 1
                        or len(info["examples"]) > 1)
            if has_more:
                with st.expander("📖 更多释义与例句"):
                    for d in info["defs"]:
                        st.markdown(f"- {d}")
                    for d in info.get("en_defs", [])[1:]:
                        st.markdown(f"- *{d}*")
                    for ex in info["examples"]:
                        st.markdown(f"**{ex['en']}**")
                        st.caption(ex["zh"])

            # 相关图片（带上中文释义一起搜，图片更贴合词义）
            first = st.session_state.get(f"img_first_{target}", 1)
            imgs = cached_images(info["word"], img_context(info), first)
            if imgs:
                st.markdown("#### 相关图片")
                cols = st.columns(len(imgs))
                for col, url in zip(cols, imgs):
                    with col:
                        st.image(url, use_container_width=True)
                if st.button("🔄 换一批图片"):
                    st.session_state[f"img_first_{target}"] = first + 12
                    st.rerun()

    # 搜索历史：只显示今天查过的词（完整历史仍然都存着）
    today_items = [e for e in history.values()
                   if e.get("last") == date.today().isoformat()]
    if today_items:
        st.divider()
        with st.expander(f"🕘 今天查过（{len(today_items)} 个词）", expanded=not target):
            items = sorted(today_items,
                           key=lambda x: x.get("count", 0), reverse=True)
            for e in items[:30]:
                c1, c2 = st.columns([5, 1])
                mark = "⭐" if e["word"] in book else ""
                c1.markdown(f"**{e['word']}** {mark}　{e.get('brief', '')}")
                c1.caption(f"查过 {e.get('count', 1)} 次 · 最近 {e.get('last', '')}")
                if c2.button("查看", key=f"hist_{e['word']}"):
                    st.session_state.view_word = e["word"]
                    st.rerun()

    # 查完一个词后把光标放回搜索框，可以直接输下一个
    if st.session_state.pop("refocus", False):
        refocus_searchbox()


# ============ 我的单词本 ============
with tab_book:
    if not book:
        st.info("单词本还是空的，去「查单词」页收藏几个吧～")
        if st.button("🔄 刷新"):
            st.session_state.book = storage.load_book()
            st.session_state.history = storage.load_history()
            st.rerun()
    else:
        # 第一行：统计（一行小字）
        st.markdown(f"📚 共 **{len(book)}** 个单词　·　"
                    f"🌱 今日待复习 **{len(storage.due_words(book))}**")

        # 第二行：刷新 + 导出
        df = pd.DataFrame(
            [{
                "单词": e["word"],
                "释义": "；".join(e["defs"]),
                "收藏日期": e["added"],
                "熟练度": e["level"],
                "下次复习": e["next_review"],
            } for e in book.values()]
        )
        b1, b2 = st.columns(2)
        if b1.button("🔄 刷新", use_container_width=True):
            # 手机和电脑同时在用时，点这里拉取最新数据
            st.session_state.book = storage.load_book()
            st.session_state.history = storage.load_history()
            st.rerun()
        b2.download_button(
            "⬇️ 导出 CSV",
            df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"单词本_{date.today().isoformat()}.csv",
            use_container_width=True,
        )

        # 第三行：排序方式 + 正序/倒序
        s1, s2 = st.columns([3, 1])
        sort_by = s1.radio(
            "排序", ["🕐 时间", "🔤 字母", "🌱 熟练度"],
            horizontal=True, label_visibility="collapsed",
        )
        desc = s2.toggle("倒序", value=(sort_by == "🕐 时间"))

        if sort_by == "🔤 字母":
            entries = sorted(book.values(), key=lambda x: x["word"].lower())
        elif sort_by == "🌱 熟练度":
            entries = sorted(book.values(),
                             key=lambda x: (x.get("level", 0), x["word"].lower()))
        else:  # 时间正序 = 早收藏的在前；倒序 = 最新的在前
            entries = sorted(book.values(), key=lambda x: x["added"])
        if desc:
            entries = entries[::-1]

        # 每行：点单词发音，点 ⋮ 打开详情内页（内页里可以左右切换单词）
        sorted_words = [e["word"] for e in entries]
        for i, e in enumerate(entries):
            c_word, c_more = st.columns([8, 1])
            with c_word:
                clickable_word(e["word"],
                               sub=simplify_def(e["defs"][0], 2) if e["defs"] else "",
                               size=19)
            with c_more:
                if st.button("⋮", key=f"more_{e['word']}"):
                    st.session_state.dlg_words = sorted_words
                    st.session_state.dlg_idx = i
                    st.session_state.dlg_open = True
                    st.rerun()


# ============ 复习 ============
with tab_review:
    due = storage.due_words(book)

    # 还没开始复习
    if "review_queue" not in st.session_state:
        if not book:
            st.info("先去收藏一些单词，才能开始复习哦～")
        elif not due:
            st.success("🎉 今天的复习任务已完成，没有到期的单词！")
        else:
            st.markdown(f"今天有 **{len(due)}** 个单词等着复习")
            if st.button("开始复习", type="primary"):
                queue = [e["word"] for e in due]
                random.shuffle(queue)
                st.session_state.review_queue = queue
                st.session_state.review_total = len(queue)
                st.session_state.review_done = 0
                st.session_state.show_answer = False
                st.rerun()
    # 复习进行中
    elif st.session_state.review_queue:
        w = st.session_state.review_queue[0]
        entry = book.get(w)
        if entry is None:  # 词条被删除的兜底
            st.session_state.review_queue.pop(0)
            st.rerun()

        st.progress(st.session_state.review_done / st.session_state.review_total,
                    text=f"进度 {st.session_state.review_done} / {st.session_state.review_total}")

        # 出题时自动朗读；点单词可以再听一遍
        clickable_word(w, size=34,
                       autoplay=not st.session_state.show_answer)

        if not st.session_state.show_answer:
            if st.button("👀 显示答案", type="primary", use_container_width=True):
                st.session_state.show_answer = True
                st.rerun()
        else:
            for d in concise_defs(entry["defs"]):
                st.markdown(f"- {d}")
            for d in entry.get("en_defs", [])[:1]:
                st.markdown(f"- *{d}*")
            if entry.get("examples"):
                ex = entry["examples"][0]
                st.markdown(f"**{ex['en']}**")
                st.caption(ex["zh"])
            if entry.get("images"):
                st.image(entry["images"][0], width=260)

            c1, c2 = st.columns(2)
            if c1.button("😊 认识", type="primary", use_container_width=True):
                storage.grade(book, w, known=True)
                st.session_state.review_queue.pop(0)
                st.session_state.review_done += 1
                st.session_state.show_answer = False
                st.rerun()
            if c2.button("😵 不认识", use_container_width=True):
                storage.grade(book, w, known=False)
                # 不认识的词放到队尾，这一轮里还会再出现
                st.session_state.review_queue.append(
                    st.session_state.review_queue.pop(0))
                st.session_state.show_answer = False
                st.rerun()
    # 本轮复习结束
    else:
        st.balloons()
        st.success(f"🎉 复习完成！本轮共复习 {st.session_state.review_total} 个单词。")
        if st.button("返回"):
            for k in ("review_queue", "review_total", "review_done", "show_answer"):
                st.session_state.pop(k, None)
            st.rerun()


# ============ 场景练习 ============
with tab_practice:
    if not book:
        st.info("先去收藏一些单词，才能开始场景练习哦～")
    else:
        st.markdown("#### 💬 自定义场景练习")
        zhipu_key = get_zhipu_key()

        # 选词：从单词本选，或直接输入任意英文单词（不限于单词本）
        c1, c2 = st.columns(2)
        picked = c1.selectbox("从单词本选", ["—"] + sorted(book.keys()))
        typed = c2.text_input("或输入英文单词", placeholder="如 gait")
        pw_word = typed.strip() or (picked if picked != "—" else "")

        if not zhipu_key:
            st.info("配置 AI 后可用自定义场景（见部署指南）")
        elif not pw_word:
            st.info("👆 从单词本选一个词，或直接输入要练习的英文单词")
        else:
            # 拿中文含义：单词本里有就用现成的，否则查一下
            src = book.get(pw_word) or cached_lookup(pw_word)
            zh = img_context(src) or pw_word

            # 推荐场景：按词类型给几个相关的，用紧凑标签（一行搞定，不占地方）
            pick_scene = st.pills(
                "推荐场景", recommend_scenes(src),
                selection_mode="single", label_visibility="collapsed")
            custom = st.text_input(
                "场景", placeholder="或自己输入场景，比如：教患者用助行器",
                label_visibility="collapsed", key="scene_custom")
            # 自己输入的优先，否则用点选的标签
            scene = custom.strip() or (pick_scene or "")

            if not scene.strip():
                st.info("👆 点一个推荐场景，或自己输入一个")
            else:
                with st.spinner("AI 正在按你的场景编写对话..."):
                    sents = cached_scene(pw_word, zh, scene.strip(), zhipu_key,
                                         ai_dialogue.SCENE_VERSION)
                if not sents:
                    st.warning("这个场景没生成出来，换个说法再试试～")
                else:
                    # 场景对话显示全部（一整段），不再砍成 2-3 句
                    for i, ex in enumerate(sents, 1):
                        st.markdown(f"{i}. {cloze(ex['en'], pw_word)}")
                        st.caption(ex["zh"])
                    if st.toggle("👀 显示原句", key="show_cloze_answer"):
                        st.divider()
                        for i, ex in enumerate(sents, 1):
                            st.markdown(f"{i}. {highlight(ex['en'], pw_word)}")

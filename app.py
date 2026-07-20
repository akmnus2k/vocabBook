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

import dict_api
import storage

st.set_page_config(page_title="PT 单词本", page_icon="📗", layout="centered")


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
]


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


def clickable_word(word, sub="", size=26, autoplay=False):
    """可点击的单词：点单词本身就发音（浏览器本地播放，零延迟）

    autoplay=True 时渲染后自动朗读一遍（浏览器拦截自动播放时静默失败，点词即可）
    """
    url = dict_api.audio_url(word)
    auto = (f"<script>new Audio('{url}').play().catch(function(){{}});</script>"
            if autoplay else "")
    sub_html = (f'<span style="font-size:14px;color:#7A8B96;margin-left:10px">'
                f'{html_lib.escape(sub)}</span>') if sub else ""
    components.html(
        f"""<div onclick="new Audio('{url}').play()" title="点击发音"
              style="cursor:pointer;font-family:'Source Sans Pro',sans-serif;
                     color:#3D4F5C;white-space:nowrap;overflow:hidden;
                     text-overflow:ellipsis;line-height:1.4">
              <span style="font-size:{size}px;font-weight:700">{html_lib.escape(word)}</span>
              {sub_html}</div>{auto}""",
        height=int(size * 1.7),
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

st.title("📗 PT 单词本")
tab_search, tab_book, tab_review, tab_practice = st.tabs(
    ["🔍 查单词", "📒 我的单词本", "🌱 复习", "🎯 场景练习"])


# ============ 查单词 ============
with tab_search:
    selected = st_searchbox(
        dict_api.suggest,
        key="word_search",
        placeholder="输入单词的前几个字母，比如 scolio ...",
        label="查词（输入时会自动联想）",
        clear_on_submit=True,  # 选完自动清空，直接输下一个词
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
                brief = info["defs"][0] if info["defs"] else ""
                threading.Thread(
                    target=storage.record_history,
                    args=(history, info["word"], brief), daemon=True,
                ).start()
                st.session_state.hist_recorded = target

            # 单词标题：点击单词本身就发音
            clickable_word(info["word"], sub="点我发音", size=28)

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
                    st.toast(f"「{info['word']}」已收藏！", icon="⭐")
                    st.rerun()

            # 中文释义
            st.markdown("#### 释义")
            for d in info["defs"]:
                st.markdown(f"- {d}")

            # 英文释义（上班时用英文向同事/病人解释就靠它）
            # 用 .get 安全取值：升级前的旧缓存结果里可能没有这个字段
            if info.get("en_defs"):
                st.markdown("#### 英文释义 English Definition")
                for d in info.get("en_defs", []):
                    st.markdown(f"- *{d}*")

            # 双语例句
            if info["examples"]:
                st.markdown("#### 例句 · 应用场景")
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

    # 搜索历史：查过的词都在这里，点一下就能回看
    if history:
        st.divider()
        with st.expander(f"🕘 搜索历史（{len(history)} 个词）", expanded=not target):
            items = sorted(history.values(),
                           key=lambda x: (x.get("last", ""), x.get("count", 0)),
                           reverse=True)
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
    c1, c2, c3 = st.columns([2, 2, 1])
    c1.metric("收藏总数", len(book))
    c2.metric("今日待复习", len(storage.due_words(book)))
    if c3.button("🔄 刷新"):
        # 手机和电脑同时在用时，点这里拉取最新数据
        st.session_state.book = storage.load_book()
        st.session_state.history = storage.load_history()
        st.rerun()

    if not book:
        st.info("单词本还是空的，去「查单词」页收藏几个吧～")
    else:
        # 备份下载
        df = pd.DataFrame(
            [{
                "单词": e["word"],
                "释义": "；".join(e["defs"]),
                "收藏日期": e["added"],
                "熟练度": e["level"],
                "下次复习": e["next_review"],
            } for e in book.values()]
        )
        st.download_button(
            "⬇️ 导出单词本 (CSV)",
            df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"单词本_{date.today().isoformat()}.csv",
        )

        st.divider()

        # 排序方式
        sort_by = st.radio(
            "排序", ["🕐 按添加时间", "🔤 按首字母", "🌱 按熟练度"],
            horizontal=True, label_visibility="collapsed",
        )
        if sort_by == "🔤 按首字母":
            entries = sorted(book.values(), key=lambda x: x["word"].lower())
        elif sort_by == "🌱 按熟练度":
            entries = sorted(book.values(),
                             key=lambda x: (x.get("level", 0), x["word"].lower()))
        else:  # 按添加时间，最新的在前
            entries = sorted(book.values(), key=lambda x: x["added"], reverse=True)

        # 每行：点单词发音 + 「详情」看释义
        for e in entries:
            stars = "🌟" * e["level"] + "☆" * (len(storage.INTERVALS) - 1 - e["level"])
            c_word, c_more = st.columns([5, 1])
            with c_word:
                clickable_word(e["word"],
                               sub=e["defs"][0] if e["defs"] else "", size=20)
            with c_more:
                with st.popover("详情"):
                    if e.get("phone_us"):
                        st.caption(f"美 /{e['phone_us']}/")
                    for d in e["defs"]:
                        st.markdown(f"- {d}")
                    for d in e.get("en_defs", []):
                        st.markdown(f"- *{d}*")
                    for ex in e.get("examples", [])[:2]:
                        st.markdown(f"**{ex['en']}**")
                        st.caption(ex["zh"])
                    st.caption(f"熟练度 {stars}")
                    st.caption(f"收藏于 {e['added']}　下次复习 {e['next_review']}")
                    if st.button("移除", key=f"del_{e['word']}"):
                        storage.remove_word(book, e["word"])
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
        clickable_word(w, sub="点我再听一遍", size=34,
                       autoplay=not st.session_state.show_answer)

        if not st.session_state.show_answer:
            if st.button("👀 显示答案", type="primary", use_container_width=True):
                st.session_state.show_answer = True
                st.rerun()
        else:
            for d in entry["defs"]:
                st.markdown(f"- {d}")
            for d in entry.get("en_defs", [])[:2]:
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
        pw_word = st.selectbox("选一个单词来练习", sorted(book.keys()))
        entry = book[pw_word]

        # —— 练习一：PT 场景填空 ——
        st.markdown("#### ✍️ PT 场景填空")
        st.caption("下面是这个词在物理治疗/康复场景里的真实句子，先看中文提示，想想空里填什么")
        with st.spinner("正在找 PT 场景例句..."):
            sents = cached_pt_sentences(pw_word)
        if not sents:  # 搜不到场景句就用收藏时存的普通例句
            sents = entry.get("examples", [])

        if not sents:
            st.info("这个词暂时没找到合适的例句，试试别的词～")
        else:
            for i, ex in enumerate(sents[:5], 1):
                st.markdown(f"{i}. {cloze(ex['en'], pw_word)}")
                st.caption(ex["zh"])
            if st.toggle("👀 显示原句", key="show_cloze_answer"):
                st.divider()
                for i, ex in enumerate(sents[:5], 1):
                    st.markdown(f"{i}. {highlight(ex['en'], pw_word)}")

        # —— 练习二：用英文解释 ——
        st.divider()
        st.markdown("#### 🗣️ 用英文解释挑战")
        st.markdown(f"想象你在向同事或病人解释 **{pw_word}**——先自己用英文说一遍，再对照参考：")
        clickable_word(pw_word, sub="点我发音", size=22)
        with st.expander("对照参考答案"):
            en_defs = entry.get("en_defs") or cached_lookup(pw_word).get("en_defs", [])
            for d in en_defs:
                st.markdown(f"- *{d}*")
            for d in entry.get("defs", []):
                st.markdown(f"- {d}")

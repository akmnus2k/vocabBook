# -*- coding: utf-8 -*-
"""PT 单词本：查词（联想）→ 收藏 → 复习，附搜索历史"""
import random
from datetime import date

import pandas as pd
import streamlit as st
from streamlit_searchbox import st_searchbox

import dict_api
import storage

st.set_page_config(page_title="PT 单词本", page_icon="📘", layout="centered")


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
    st.title("📘 PT 单词本")
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


@st.cache_data(ttl=3600, show_spinner=False)
def cached_images(word):
    return dict_api.get_images(word, n=3)


# 单词本和搜索历史整个会话只从存储读一次，之后在内存里改、随手写回存储
if "book" not in st.session_state:
    st.session_state.book = storage.load_book()
if "history" not in st.session_state:
    st.session_state.history = storage.load_history()
book = st.session_state.book
history = st.session_state.history

st.title("📘 PT 单词本")
tab_search, tab_book, tab_review = st.tabs(["🔍 查单词", "📒 我的单词本", "🌱 复习"])


# ============ 查单词 ============
with tab_search:
    selected = st_searchbox(
        dict_api.suggest,
        key="word_search",
        placeholder="输入单词的前几个字母，比如 scolio ...",
        label="查词（输入时会自动联想）",
    )
    # 搜索框有新选择时，切换到这个词（历史记录里点词也会切换，见下面）
    if selected and selected != st.session_state.get("prev_search"):
        st.session_state.prev_search = selected
        st.session_state.view_word = selected

    target = st.session_state.get("view_word")

    if target:
        with st.spinner("查询中..."):
            info = cached_lookup(target)

        if not info["found"]:
            st.warning(f"没有查到「{target}」，检查一下拼写？")
        else:
            # 自动记入搜索历史（同一个词一次会话只记一笔）
            if st.session_state.get("hist_recorded") != target:
                brief = info["defs"][0] if info["defs"] else ""
                storage.record_history(history, info["word"], brief)
                st.session_state.hist_recorded = target

            st.subheader(info["word"])

            # 音标 + 发音
            phones = []
            if info["phone_us"]:
                phones.append(f"美 /{info['phone_us']}/")
            if info["phone_uk"]:
                phones.append(f"英 /{info['phone_uk']}/")
            if phones:
                st.caption("　".join(phones))
            st.audio(dict_api.audio_url(info["word"]), format="audio/mpeg")

            # 中文释义
            st.markdown("#### 释义")
            for d in info["defs"]:
                st.markdown(f"- {d}")

            # 英文释义（上班时用英文向同事/病人解释就靠它）
            if info["en_defs"]:
                st.markdown("#### 英文释义 English Definition")
                for d in info["en_defs"]:
                    st.markdown(f"- *{d}*")

            # 双语例句
            if info["examples"]:
                st.markdown("#### 例句 · 应用场景")
                for ex in info["examples"]:
                    st.markdown(f"**{ex['en']}**")
                    st.caption(ex["zh"])

            # 相关图片
            imgs = cached_images(info["word"])
            if imgs:
                st.markdown("#### 相关图片")
                cols = st.columns(len(imgs))
                for col, url in zip(cols, imgs):
                    with col:
                        st.image(url, use_container_width=True)

            st.divider()

            # 收藏 / 已收藏
            if info["word"] in book:
                st.success("✅ 已在单词本里")
                if st.button("从单词本移除"):
                    storage.remove_word(book, info["word"])
                    st.rerun()
            else:
                if st.button("⭐ 收藏到单词本", type="primary"):
                    storage.add_word(book, info, imgs)
                    st.toast(f"「{info['word']}」已收藏！", icon="⭐")
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
        # 最新收藏的排在前面
        for e in sorted(book.values(), key=lambda x: x["added"], reverse=True):
            stars = "🌟" * e["level"] + "☆" * (len(storage.INTERVALS) - 1 - e["level"])
            with st.expander(f"**{e['word']}**　{e['defs'][0] if e['defs'] else ''}"):
                if e.get("phone_us"):
                    st.caption(f"美 /{e['phone_us']}/")
                st.audio(dict_api.audio_url(e["word"]), format="audio/mpeg")
                for d in e["defs"]:
                    st.markdown(f"- {d}")
                for d in e.get("en_defs", []):
                    st.markdown(f"- *{d}*")
                for ex in e.get("examples", [])[:2]:
                    st.markdown(f"**{ex['en']}**")
                    st.caption(ex["zh"])
                st.caption(f"熟练度 {stars}　|　收藏于 {e['added']}　|　下次复习 {e['next_review']}")
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

        st.markdown(f"## {w}")
        st.audio(dict_api.audio_url(w), format="audio/mpeg")

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

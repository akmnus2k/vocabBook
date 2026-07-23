# -*- coding: utf-8 -*-
"""PT 单词本：查词（联想）→ 收藏 → 复习 → 场景练习，附搜索历史"""
import html as html_lib
import json
import random
import re
import threading

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

# 手机"添加到主屏幕"的图标：Streamlit 默认用自家红色 logo，这里换成
# static/icon.png（浅蓝底绿书）。page_icon 只管浏览器标签页，管不到主屏幕图标：
# iOS 认 apple-touch-icon，安卓 Chrome 优先认 manifest，普通收藏认 icon——
# 三样都指向新图标，并顺手删掉 Streamlit 自带的红 logo favicon 链接。
components.html("""<script>
var doc = window.parent.document;
doc.querySelectorAll(
    'link[rel~="icon"], link[rel="apple-touch-icon"], link[rel="mask-icon"], link[rel="manifest"]'
).forEach(function (el) { el.remove(); });
[["apple-touch-icon", "./app/static/icon.png"],
 ["icon", "./app/static/icon.png"],
 ["manifest", "./app/static/manifest.json"]].forEach(function (pair) {
    var link = doc.createElement("link");
    link.rel = pair[0];
    if (pair[0] !== "manifest") link.sizes = "512x512";
    link.href = pair[1];
    doc.head.appendChild(link);
});
</script>""", height=0)


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


def get_scene(word, zh, scene, entry, api_key):
    """场景对话：词条里缓存过（存进 Sheet）就秒回，否则生成并存回词条"""
    ver = ai_dialogue.SCENE_VERSION
    if entry and entry.get("scenes_ver") == ver:
        cached = entry.get("scenes", {}).get(scene)
        if cached:
            return cached
    if not api_key:
        return None
    d = cached_scene(word, zh, scene, api_key, ver)
    if d and entry is not None:  # 已收藏的词把这个场景存进 Sheet，下次秒开
        if entry.get("scenes_ver") != ver:
            entry["scenes"] = {}
            entry["scenes_ver"] = ver
        entry.setdefault("scenes", {})[scene] = d
        storage.save_book(book)
    return d


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


def get_ai_key():
    """AI 例句/对话生成用的 Kimi key"""
    try:
        return st.secrets.get("kimi_api_key", "")
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


def _audio_js(text, sentence=False):
    """生成一段 JS：按音源链依次尝试播放，全失败才退回浏览器语音合成

    单词链：有道真人音 → Google 合成音 → 百度合成音 → 语音合成
    整句链：Google 合成音 → 百度合成音 → 语音合成
      （有道 dictvoice 读不了任意整句，跳过它省一次失败等待；Google 比百度
       自然流畅，作整句首选，读不了时才退百度。）
    语音合成是最后手段——有的设备（如没装英文语音包的电脑）读不了英文。
    """
    text_js = json.dumps(text)  # 安全地转成 JS 字符串字面量
    if sentence:
        srcs = [dict_api.google_tts_url(text), dict_api.sentence_audio_url(text)]
    else:
        srcs = [dict_api.audio_url(text), dict_api.google_tts_url(text),
                dict_api.sentence_audio_url(text)]
    srcs_js = json.dumps(srcs)
    return (f"var s={srcs_js},i=0;"
            f"function n(){{if(i>=s.length){{"
            f"var u=new SpeechSynthesisUtterance({text_js});u.lang='en-GB';"
            f"speechSynthesis.cancel();speechSynthesis.speak(u);return;}}"
            f"var a=new Audio(s[i]);i=i+1;a.onerror=n;"
            f"a.play().catch(function(){{}});}}n();")


def clickable_word(word, sub="", size=26, autoplay=False):
    """可点击的单词：点单词本身就发音

    首选有道词典的真人发音（英音），音频加载失败时才退回浏览器内置的
    语音合成（Web Speech API）兜底——保证冷僻词/断网时也能出声。
    autoplay=True 时渲染后自动读一遍（浏览器可能拦截自动播放，点词即可）。
    """
    # 发音逻辑放进具名函数，onclick 只调用它——否则 JS 里的引号会和
    # onclick="..." 的属性引号打架，把处理器截断成一句残缺代码（点了没反应）
    # no-referrer：百度音频拒绝带外站 Referer 的请求，不发它就正常返回
    script = ('<meta name="referrer" content="no-referrer">'
              f"<script>function playWord(){{{_audio_js(word)}}}</script>")
    auto = "<script>playWord()</script>" if autoplay else ""
    sub_html = (f'<span style="font-size:14px;color:#7A8B96;margin-left:10px">'
                f'{html_lib.escape(sub)}</span>') if sub else ""
    # 单词加一条浅蓝虚线下划线，暗示"可以点"，不用再写"点我发音"
    components.html(
        f"""{script}<div onclick="playWord()" title="点击发音"
              style="cursor:pointer;font-family:'Source Sans Pro',sans-serif;
                     color:#3D4F5C;white-space:nowrap;overflow:hidden;
                     text-overflow:ellipsis;line-height:1.5">
              <span style="font-size:{size}px;font-weight:700;
                     border-bottom:2px dashed #A8D4EA;padding-bottom:1px">
                     {html_lib.escape(word)}</span>
              {sub_html}</div>{auto}""",
        height=int(size * 1.8),
    )


def speaker_only(text, size=44, autoplay=True, sentence=False):
    """只放一个 🔊 图标：点了发音。听音辨词题型和整句朗读都用它。"""
    script = ('<meta name="referrer" content="no-referrer">'
              f"<script>function playWord(){{{_audio_js(text, sentence)}}}</script>")
    auto = "<script>playWord()</script>" if autoplay else ""
    components.html(
        f"""{script}<div onclick="playWord()" title="再听一遍"
              style="cursor:pointer;text-align:center;line-height:1.4;
                     font-size:{size}px;user-select:none">🔊</div>{auto}""",
        height=int(size * 1.6),
    )


# 连播播放器：整个是自包含的 JS 播放器（音频队列在浏览器里跑），不依赖
# Streamlit 刷新——所以连续播放不会被打断。__PLAYLIST__ 处注入播放列表。
_PLAYER_HTML = r"""<meta name="referrer" content="no-referrer">
<style>
 #pbox{font-family:'Source Sans Pro',sans-serif;background:#EAF4FA;border-radius:14px;
       padding:14px 16px;color:#3D4F5C;text-align:center}
 #now{font-size:30px;font-weight:700;line-height:1.2;white-space:nowrap;
      overflow:hidden;text-overflow:ellipsis}
 #sub{font-size:14px;color:#5B7183;min-height:20px;margin:2px auto 0;line-height:1.35;
      max-width:94%;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;
      overflow:hidden}
 .pbtn{border:none;background:#CDE7F5;color:#2C3E4C;border-radius:50%;width:42px;height:42px;
       font-size:18px;cursor:pointer;vertical-align:middle;margin:0 5px}
 .pbtn.big{width:54px;height:54px;font-size:24px;background:#A8D4EA}
 .pbtn:hover{filter:brightness(0.96)}
 #opts{font-size:13px;color:#5B7183;display:flex;gap:16px;justify-content:center;margin-top:4px}
 #opts label{cursor:pointer}
</style>
<div id="pbox">
  <div id="now">—</div>
  <div id="sub">点 ▶ 开始，自动逐个播放单词和例句</div>
  <div style="margin:10px 0 6px">
    <button class="pbtn" id="prev">⏮</button>
    <button class="pbtn big" id="pp">▶</button>
    <button class="pbtn" id="next">⏭</button>
    <span id="prog" style="font-size:13px;color:#5B7183;margin-left:8px">0 / 0</span>
  </div>
  <div id="opts">
    <label><input type="checkbox" id="sent" checked> 含例句</label>
    <label><input type="checkbox" id="loop" checked> 循环</label>
    <label><input type="checkbox" id="shuf"> 随机</label>
  </div>
</div>
<script>
var LIST=__PLAYLIST__;
var order=[],idx=0,phase='w',playing=false,cur=null,timer=null;
var $=function(id){return document.getElementById(id);};
function shuffle(a){for(var i=a.length-1;i>0;i--){var j=Math.floor(Math.random()*(i+1));var t=a[i];a[i]=a[j];a[j]=t;}return a;}
function build(){order=LIST.map(function(_,i){return i;});if($('shuf').checked)shuffle(order);}
function it(){return LIST[order[idx]];}
function media(){
  if(!('mediaSession' in navigator)||!order.length)return;
  var x=it();
  try{
    navigator.mediaSession.metadata=new MediaMetadata({title:x.w,artist:(x.d||'PT 单词本'),album:'PT 单词本 · 循环播放'});
    navigator.mediaSession.playbackState=playing?'playing':'paused';
  }catch(e){}
}
function render(){
  if(!order.length)return;
  var x=it();
  $('now').textContent=x.w;
  $('sub').textContent=(phase==='s'&&x.s)?('💬 '+x.s):(x.d?('🔊 '+x.d):'🔊');
  $('prog').textContent=(idx+1)+' / '+order.length;
  $('pp').textContent=playing?'⏸':'▶';
  media();
}
function stopAudio(){if(cur){cur.onended=null;cur.onerror=null;try{cur.pause();}catch(e){}cur=null;}if(timer){clearTimeout(timer);timer=null;}}
function playUrls(urls,onEnd){
  var k=0;
  (function one(){
    if(!playing)return;
    if(k>=urls.length){onEnd();return;}
    var a=new Audio(urls[k]);k++;cur=a;
    a.onerror=function(){one();};
    a.onended=function(){cur=null;onEnd();};
    a.play().catch(function(){});
  })();
}
function afterWord(){
  if(!playing)return;
  var x=it();
  if($('sent').checked&&x.s){phase='s';render();timer=setTimeout(function(){playUrls(x.su,afterSent);},350);}
  else afterSent();
}
function afterSent(){if(playing)timer=setTimeout(nextWord,500);}
function nextWord(){
  if(!playing)return;
  if(idx<order.length-1)idx++;
  else if($('loop').checked){idx=0;build();}
  else{playing=false;render();return;}
  phase='w';render();step();
}
function step(){
  if(!playing||!order.length)return;
  var x=it();
  if(phase==='s')playUrls(x.su,afterSent);
  else playUrls(x.wu,afterWord);
}
$('pp').onclick=function(){
  if(playing){playing=false;stopAudio();render();return;}
  if(!order.length)build();
  playing=true;render();step();
};
$('prev').onclick=function(){stopAudio();idx=Math.max(0,idx-1);phase='w';render();if(playing)step();};
$('next').onclick=function(){stopAudio();if(idx<order.length-1)idx++;else if($('loop').checked){idx=0;build();}phase='w';render();if(playing)step();};
$('shuf').onchange=function(){stopAudio();idx=0;build();phase='w';render();if(playing)step();};
// 锁屏/通知栏的媒体控制（安卓上还能帮助维持后台播放；iOS 支持有限）
if('mediaSession' in navigator){try{
  navigator.mediaSession.setActionHandler('play',function(){if(!playing){if(!order.length)build();playing=true;render();step();}});
  navigator.mediaSession.setActionHandler('pause',function(){playing=false;stopAudio();render();});
  navigator.mediaSession.setActionHandler('previoustrack',function(){$('prev').click();});
  navigator.mediaSession.setActionHandler('nexttrack',function(){$('next').click();});
}catch(e){}}
build();render();
</script>"""


def audio_player(entries):
    """把词条列表做成一个连播播放器：逐个播'单词发音 + 例句发音'，可循环/随机"""
    playlist = []
    for e in entries:
        item = {
            "w": e["word"],
            "d": simplify_def(e["defs"][0], 1) if e.get("defs") else "",
            "wu": [dict_api.audio_url(e["word"]),
                   dict_api.sentence_audio_url(e["word"])],
        }
        exs = e.get("dialogues") or e.get("examples")
        if exs:
            item["s"] = exs[0]["en"]
            # 例句同样先试有道真人音、再退百度
            item["su"] = [dict_api.audio_url(exs[0]["en"]),
                          dict_api.sentence_audio_url(exs[0]["en"])]
        playlist.append(item)
    html = _PLAYER_HTML.replace("__PLAYLIST__",
                                json.dumps(playlist, ensure_ascii=False))
    # 高度留够：例句可换行到 3 行，窄屏也不会被截成"…"
    components.html(html, height=260)


# 复习题型：cn 看词想义、en 看义猜词、cloze 例句填空、listen 听音辨词
QUIZ_LABELS = {"看词想义": "cn", "看义猜词": "en",
               "例句填空": "cloze", "听音辨词": "listen"}
# 答对时随机蹦一句彩虹屁，给点即时反馈
PRAISE = ["🎯 记得牢！", "⚡ 秒答！", "🌟 就是它！", "👍 稳！", "🍀 漂亮！", "🧠 好记性！"]


def pick_quiz_mode(entry, allowed):
    """在允许的题型里随机挑一个；没有例句的词跳过「例句填空」"""
    modes = [m for m in allowed if m != "cloze"
             or entry.get("dialogues") or entry.get("examples")]
    return random.choice(modes) if modes else "cn"


def tappable_sentence(en, zh, prefix=""):
    """例句：整句正常显示，左侧一个小喇叭朗读整句

    喇叭走"有道真人音优先、百度合成音兜底"——整句一般走百度。
    prefix 参数保留是为了兼容旧调用，现已不再需要。
    """
    # 喇叭对齐例句第一行（top）：例句换行时喇叭不会跑到中间那行去
    c_spk, c_txt = st.columns([1, 9], vertical_alignment="top")
    with c_spk:
        speaker_only(en, size=18, autoplay=False, sentence=True)
    with c_txt:
        st.markdown(f"**{en}**")
        st.caption(zh)


def reveal_details(entry, defs=True, example=True):
    """揭晓答案时展示的详情：中文释义 / 英文释义 / 例句 / 图片"""
    if defs:
        for d in concise_defs(entry["defs"]):
            st.markdown(f"- {d}")
        for d in entry.get("en_defs", [])[:1]:
            st.markdown(f"- *{d}*")
    # 优先 AI 诊室例句（翻译贴近场景、质量稳定），没有才退回有道例句库
    exs = entry.get("dialogues") or entry.get("examples")
    if example and exs:
        tappable_sentence(exs[0]["en"], exs[0]["zh"], prefix="rv")
    if entry.get("images"):
        st.image(entry["images"][0], width=260)


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
start_sweep(book, get_ai_key())


@st.dialog("词条详情")
def word_detail_dialog():
    """单词本里点 ⋮ 弹出的内页，左右按钮切换上一个/下一个单词"""
    words = st.session_state.get("dlg_words", [])
    idx = max(0, min(st.session_state.get("dlg_idx", 0), len(words) - 1))
    e = book.get(words[idx]) if words else None
    if e is None:
        st.info("这个词已经不在单词本里了")
        return

    # 单词在左，右上角一个小垃圾桶移除（窄小、不占整行，避免误触）
    c_word, c_del = st.columns([5, 1], vertical_alignment="center")
    with c_word:
        clickable_word(e["word"], size=24)
    if c_del.button("🗑️", key="del_word", help="从单词本移除"):
        storage.remove_word(book, e["word"])
        st.rerun()
    ph = e.get("phone_uk") or e.get("phone_us")
    if ph:
        st.caption(f"英 /{ph}/")
    for d in concise_defs(e["defs"]):
        st.markdown(f"- {d}")
    if e.get("en_defs"):
        st.markdown(f"🗣️ *{e['en_defs'][0]}*")
    # 优先 AI 诊室例句（翻译贴近场景、质量稳定），没有才退回有道例句库
    exs = e.get("dialogues") or e.get("examples") or []
    for ex in exs[:1]:
        tappable_sentence(ex["en"], ex["zh"], prefix="dlg")
    stars = "🌟" * e["level"] + "☆" * (len(storage.INTERVALS) - 1 - e["level"])
    st.caption(f"熟练度 {stars}　|　收藏于 {e['added']}　|　下次复习 {e['next_review']}")

    # 最下排：左右切换上一个/下一个单词
    nav_l, nav_mid, nav_r = st.columns([1, 2, 1], vertical_alignment="center")
    if nav_l.button("⬅️", disabled=(idx == 0), use_container_width=True):
        st.session_state.dlg_idx = idx - 1
        st.session_state.dlg_open = True
        st.rerun()
    nav_mid.markdown(
        f"<div style='text-align:center;color:#7A8B96'>{idx + 1} / {len(words)}</div>",
        unsafe_allow_html=True)
    if nav_r.button("➡️", disabled=(idx == len(words) - 1), use_container_width=True):
        st.session_state.dlg_idx = idx + 1
        st.session_state.dlg_open = True
        st.rerun()


# 弹窗采用"一次性标记"：内页里点了左右切换会重新打开；点 X 关闭就真的关掉
if st.session_state.pop("dlg_open", False):
    word_detail_dialog()

# 标题即导出入口：点「📗 PT 单词本」弹出导出面板。刷新按钮去掉——每次进
# 页面都会自动读最新数据，要多设备同步刷新整个页面即可。
with st.popover("📗 PT 单词本"):
    if book:
        st.download_button(
            "⬇️ 导出为 CSV",
            pd.DataFrame(
                [{"单词": e["word"],
                  "释义": "；".join(e["defs"]),
                  "收藏日期": e["added"],
                  "熟练度": e["level"],
                  "下次复习": e["next_review"]} for e in book.values()]
            ).to_csv(index=False).encode("utf-8-sig"),
            file_name=f"单词本_{storage.today_iso()}.csv",
            use_container_width=True,
        )
        st.caption(f"共 {len(book)} 个单词，含释义 / 收藏日期 / 熟练度")
    else:
        st.caption("单词本还空着，收藏几个词就能导出啦～")
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

            # 音标：她在澳洲，只显示英式；没有英式才退回美式
            phone = info["phone_uk"] or info["phone_us"]
            if phone:
                st.caption(f"英 /{phone}/")

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
                                 get_ai_key())
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
                    entry_in_book, get_ai_key())
            if clinic:
                for i, ex in enumerate(clinic[:2]):
                    tappable_sentence(ex["en"], ex["zh"], prefix=f"se{i}")
            elif info["examples"]:
                ex = info["examples"][0]
                tappable_sentence(ex["en"], ex["zh"], prefix="se_yd")
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
                   if e.get("last") == storage.today_iso()]
    if today_items:
        st.divider()
        with st.expander(f"🕘 今天查过（{len(today_items)} 个词）", expanded=not target):
            # 最近查的排最前（按查询时间倒序）
            items = sorted(today_items,
                           key=lambda x: x.get("last_ts", ""), reverse=True)
            for e in items[:30]:
                c1, c2 = st.columns([5, 1])
                mark = "⭐" if e["word"] in book else ""
                # 只显示几点几分，不显示日期
                hm = e.get("last_ts", "")[11:16]
                c1.markdown(f"**{e['word']}** {mark}　{e.get('brief', '')}")
                c1.caption(f"查过 {e.get('count', 1)} 次 · {hm}")
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
    else:
        # 第一行：统计（一行小字）
        st.markdown(f"📚 共 **{len(book)}** 个单词　·　"
                    f"🌱 今日待复习 **{len(storage.due_words(book))}**")

        # 第二行：排序下拉 + 倒序开关都靠左聚拢、右侧留白，和上面统计行的
        # 左对齐风格一致；倒序紧跟下拉，不再孤零零贴在最右
        c_sort, c_desc, _sp = st.columns([3, 2, 3], vertical_alignment="center")
        sort_by = c_sort.selectbox(
            "排序", ["🕐 时间", "🔤 字母", "🌱 熟练度"],
            label_visibility="collapsed")
        desc = c_desc.toggle("倒序", value=(sort_by == "🕐 时间"))

        if sort_by == "🔤 字母":
            entries = sorted(book.values(), key=lambda x: x["word"].lower())
        elif sort_by == "🌱 熟练度":
            entries = sorted(book.values(),
                             key=lambda x: (x.get("level", 0), x["word"].lower()))
        else:  # 时间正序 = 早收藏的在前；倒序 = 最新的在前
            entries = sorted(book.values(), key=lambda x: x["added"])
        if desc:
            entries = entries[::-1]

        # 连播播放器：磨耳朵用，按当前排序逐个播单词+例句，可循环/随机
        with st.expander(f"🎧 循环播放（{len(entries)} 个单词）"):
            audio_player(entries)

        # 每行：单词 + 紧挨的释义（点它们发音），右边一个「查看」按钮展开详情
        sorted_words = [e["word"] for e in entries]
        for i, e in enumerate(entries):
            c_word, c_btn = st.columns([8, 2], vertical_alignment="center")
            with c_word:
                clickable_word(
                    e["word"],
                    sub=simplify_def(e["defs"][0], 2) if e["defs"] else "",
                    size=19)
            with c_btn:
                if st.button("查看", key=f"more_{e['word']}",
                             use_container_width=True):
                    st.session_state.dlg_words = sorted_words
                    st.session_state.dlg_idx = i
                    st.session_state.dlg_open = True
                    st.rerun()


# ============ 复习 ============
REVIEW_KEYS = ("review_queue", "review_total", "review_done",
               "review_streak", "review_allowed", "show_answer")

with tab_review:
    # 还没开始：先让用户挑范围、题量、题型，再开始
    if "review_queue" not in st.session_state:
        if not book:
            st.info("先去收藏一些单词，才能开始复习哦～")
        else:
            due = storage.due_words(book)
            st.markdown(f"今日待复习 **{len(due)}** 个　·　单词本共 **{len(book)}** 个")

            # 范围：今日复习 / 全部单词 / 生词优先（等级低的排前面）
            scope = st.pills("复习范围",
                             ["今日复习", "全部单词", "生词优先"],
                             default="今日复习", selection_mode="single") or "今日复习"
            if scope == "今日复习":
                pool = list(due)
            elif scope == "生词优先":
                # 等级越低越生，排前面；同等级按收藏早晚
                pool = sorted(book.values(),
                              key=lambda e: (e.get("level", 0), e.get("added", "")))
            else:
                pool = list(book.values())

            # 题量
            count_label = st.pills("题量", ["10 个", "20 个", "全部"],
                                   default="全部", selection_mode="single") or "全部"
            limit = {"10 个": 10, "20 个": 20}.get(count_label, len(pool))

            # 题型：可多选，默认四种混合，制造随机性
            picked_modes = st.pills(
                "题型（可多选，混着来更有意思）",
                list(QUIZ_LABELS.keys()),
                default=list(QUIZ_LABELS.keys()), selection_mode="multi")
            allowed = [QUIZ_LABELS[m] for m in (picked_modes or ["看词想义"])]

            if not pool:
                st.success("🎉 这个范围里没有可复习的单词！")
            elif st.button("开始复习", type="primary"):
                # 生词优先按顺序取前 N（保住"最生的"），其余范围随机抽 N
                if scope == "生词优先":
                    chosen = pool[:limit]
                else:
                    chosen = random.sample(pool, min(limit, len(pool)))
                random.shuffle(chosen)
                st.session_state.review_queue = [
                    {"word": e["word"], "mode": pick_quiz_mode(e, allowed)}
                    for e in chosen]
                st.session_state.review_allowed = allowed
                st.session_state.review_total = len(chosen)
                st.session_state.review_done = 0
                st.session_state.review_streak = 0
                st.session_state.show_answer = False
                st.rerun()
    # 复习进行中
    elif st.session_state.review_queue:
        card = st.session_state.review_queue[0]
        w, mode = card["word"], card["mode"]
        entry = book.get(w)
        if entry is None:  # 词条被删除的兜底
            st.session_state.review_queue.pop(0)
            st.rerun()

        show = st.session_state.show_answer
        streak = st.session_state.review_streak
        st.progress(
            st.session_state.review_done / st.session_state.review_total,
            text=(f"进度 {st.session_state.review_done} / {st.session_state.review_total}"
                  + (f"　🔥 连对 {streak}" if streak >= 2 else "")))

        # ---- 题面：按题型出不同的题 ----
        if mode == "cn":
            st.caption("🧠 看英文，想想它的中文意思")
            clickable_word(w, size=34, autoplay=not show)
        elif mode == "en":
            st.caption("🔤 看中文，猜是哪个英文词")
            for d in concise_defs(entry["defs"]):
                st.markdown(f"### {d}")
            if show:
                clickable_word(w, size=34, autoplay=True)
        elif mode == "cloze":
            st.caption("✏️ 填出例句里空缺的词")
            # 优先 Kimi 诊室例句，没有才退回有道例句库
            ex = (entry.get("dialogues") or entry.get("examples"))[0]
            if show:
                st.markdown(f"### {highlight(ex['en'], w)}")
                clickable_word(w, size=30, autoplay=True)
            else:
                st.markdown(f"### {cloze(ex['en'], w)}")
                st.caption(ex["zh"])
        else:  # listen
            st.caption("👂 听发音，猜猜是哪个词")
            speaker_only(w, autoplay=not show)
            if show:
                clickable_word(w, size=34, autoplay=False)

        # ---- 揭晓答案 + 打分 ----
        if not show:
            if st.button("👀 显示答案", type="primary", use_container_width=True):
                st.session_state.show_answer = True
                st.rerun()
        else:
            # 各题型该补的详情：填空别重复例句，看义猜词别重复中文释义
            reveal_details(entry,
                           defs=(mode != "en"),
                           example=(mode != "cloze"))

            c1, c2 = st.columns(2)
            if c1.button("😊 认识", type="primary", use_container_width=True):
                storage.grade(book, w, known=True)
                st.session_state.review_queue.pop(0)
                st.session_state.review_done += 1
                st.session_state.review_streak += 1
                st.session_state.show_answer = False
                st.toast(random.choice(PRAISE))
                st.rerun()
            if c2.button("😵 不认识", use_container_width=True):
                storage.grade(book, w, known=False)
                st.session_state.review_streak = 0
                # 不认识的放回队尾，并换个题型再考一遍
                card = st.session_state.review_queue.pop(0)
                card["mode"] = pick_quiz_mode(entry, st.session_state.review_allowed)
                st.session_state.review_queue.append(card)
                st.session_state.show_answer = False
                st.toast("再看一遍就记住啦 💪")
                st.rerun()
    # 本轮复习结束
    else:
        st.balloons()
        st.success(f"🎉 复习完成！本轮共复习 {st.session_state.review_total} 个单词。")
        if st.button("返回"):
            for k in REVIEW_KEYS:
                st.session_state.pop(k, None)
            st.rerun()


# ============ 场景练习 ============
def render_scene(scene_name, sents, word, key):
    """展示一个场景练习：挖空版 + 可展开的原句（原句可点 🔊 朗读整句）"""
    st.markdown(f"**🎬 {scene_name}**")
    for i, ex in enumerate(sents, 1):
        st.markdown(f"{i}. {cloze(ex['en'], word)}")
        st.caption(ex["zh"])
    if st.toggle("👀 显示原句", key=f"show_{key}"):
        for ex in sents:
            tappable_sentence(ex["en"], ex["zh"])


with tab_practice:
    if not book:
        st.info("先去收藏一些单词，才能开始场景练习哦～")
    else:
        st.markdown("#### 💬 场景练习")
        ai_key = get_ai_key()

        # 选词：从单词本选，或直接输入任意英文单词（不限于单词本）
        c1, c2 = st.columns(2)
        picked = c1.selectbox("从单词本选", ["—"] + sorted(book.keys()))
        typed = c2.text_input("或输入英文单词", placeholder="如 gait")
        pw_word = typed.strip() or (picked if picked != "—" else "")

        if not ai_key:
            st.info("配置 AI 后可用场景练习（见部署指南）")
        elif not pw_word:
            st.info("👆 从单词本选一个词，或直接输入要练习的英文单词")
        else:
            # 拿中文含义：单词本里有就用现成的，否则查一下
            src = book.get(pw_word) or cached_lookup(pw_word)
            zh = img_context(src) or pw_word
            entry = book.get(pw_word)  # 在单词本里才把场景对话缓存进 Sheet

            # 自动为这个词最贴合的 2 个常见场景生成对话，不用手动选
            for scene in recommend_scenes(src)[:2]:
                with st.spinner(f"生成「{scene}」对话…"):
                    sents = get_scene(pw_word, zh, scene, entry, ai_key)
                if sents:
                    render_scene(scene, sents, pw_word, key=f"{pw_word}|{scene}")
                st.divider()

            # 想练别的场景，自己输入
            custom = st.text_input(
                "想练别的场景？自己输入",
                placeholder="如：教患者用助行器", key="scene_custom")
            if custom.strip():
                with st.spinner("按你的场景生成对话…"):
                    sents = get_scene(pw_word, zh, custom.strip(), entry, ai_key)
                if sents:
                    render_scene(custom.strip(), sents, pw_word,
                                 key=f"{pw_word}|custom")
                else:
                    st.warning("这个场景没生成出来，换个说法再试试～")

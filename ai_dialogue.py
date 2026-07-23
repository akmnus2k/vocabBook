# -*- coding: utf-8 -*-
"""AI 生成诊室对话（Kimi / Moonshot，OpenAI 兼容接口）

根据单词的真实含义现编简短、地道的治疗师口语，指令遵循强、能按词性切换口吻。
配置 kimi_api_key 后启用；没配置或调用失败时调用方会退回语料例句。
"""
import json

import requests

API_URL = "https://api.moonshot.cn/v1/chat/completions"

# 提示词版本号：改了提示词就 +1，让应用端的缓存失效、重新生成
PROMPT_VERSION = 6

PROMPT = """你是美国社区诊所里一位物理治疗师。\
请用英文单词「{word}」（中文含义：{zh}）写 3 句真实、地道的口语。

**先判断这个词属于哪一类，再决定对谁说、怎么说**，这样才自然：

① 诊断/病症名（如肺不张、脊柱侧凸、椎间盘突出）——病人不会主动说这种词，\
用「治疗师向病人解释这个诊断」的口吻。
   ✅ "Part of your lung isn't fully opening up—that's atelectasis."
   ❌ "Do you have atelectasis?"（病人不可能这样被问）

② 症状/感觉/动作/身体部位（如疼痛、麻木、拉伸、膝盖、步态）——直接用在\
问诊或指导病人里，说得越简单越好。
   ✅ "Does your knee still feel stiff?"
   ✅ "Let's stretch your calf a little."

③ 器械/辅助工具（如拐杖、护具）——用在指导病人使用的场景。
   ✅ "Let me show you how to hold the cane."

④ 学术/机制术语（如 afferent 传入、efferent 传出、本体感觉、离心收缩，\
这类描述神经或生理机制的词）——患者听不懂、也感觉不到。碰到这类词，请把 3 句\
【全部】写成「治疗师给学生/实习生讲解这个概念」或「跟同事讨论某个患者」，\
不要再对患者本人说话。
   ✅（讲解）"Afferent nerves carry sensory signals from the muscles to the brain."
   ✅（讲解）"The afferent side is sensory, the efferent side is motor."
   ✅（同事）"His poor balance probably comes from reduced afferent input."
   ❌ "How are your afferent pathways feeling today?"（不能问患者感受）
   ❌ "Can you feel the afferent signals from your leg?"（患者根本感觉不到，假）

❌ 无论对谁，都别用生硬书面腔：
- "It is important to maintain proper postural alignment."
- "This intervention will facilitate functional recovery."

硬性要求：
1. 每句自然用到 {word}（可用常见变形），语法正确
2. 句子短、口语、多用缩写（it's / let's / you're），别超过 14 个词
3. 三句尽量场景不同
4. 中文翻译也要口语化，像当面说话

只输出 JSON 数组，不要任何其他文字，格式：
[{{"en": "英文句子", "zh": "中文翻译"}}]"""


# 自定义场景对话的版本号：改了 SCENE_PROMPT 就 +1，让缓存失效重新生成
SCENE_VERSION = 4

# 自定义场景的提示词：围绕用户给的场景，生成一小段连贯对话
SCENE_PROMPT = """你是美国社区诊所里一位物理治疗师。\
请围绕这个具体场景「{scene}」，用英文单词「{word}」（中文含义：{zh}）\
写 3 句像一小段真实对话的日常口语。

特别注意：如果「{word}」是患者听不懂、也感觉不到的学术/机制术语（如 afferent \
传入、efferent 传出这类描述神经或生理机制的词），就【无视场景里对象是患者】，\
把 3 句全部改成治疗师给学生讲解这个概念、或和同事讨论病例的口吻——绝不能问患者\
"你的 afferent 怎么样""你能感觉到 afferent 吗"这种句子（患者根本感觉不到，很假）。

要求：
1. 每句自然用到 {word}（可用常见变形），语法正确
2. 口语自然、像当面聊天，多用缩写
3. **三句必须各说各的、角度不同**（比如一句提问、一句指导、一句解释），\
不要三句意思重复、句式雷同
4. 中文翻译也要口语化，像当面说话

只输出 JSON 数组，不要任何其他文字，格式：
[{{"en": "英文句子", "zh": "中文翻译"}}]"""


# Kimi 模型：kimi-k2.6 指令遵循强，能按词性切换口吻。它默认是"推理模型"，
# 会先思考几十秒、还烧很多思考 token；关掉思考（thinking=disabled）后 ~5 秒出结果、
# 几乎不烧思考 token，质量照样好。关思考时该模型要求 temperature=0.6。
MODEL = "kimi-k2.6"


def _call(prompt_text, api_key, n):
    """调 AI 并解析出 [{en, zh}] 列表，失败返回 None"""
    try:
        r = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt_text}],
                "temperature": 0.6,
                "thinking": {"type": "disabled"},
            },
            timeout=30,
        )
        text = r.json()["choices"][0]["message"]["content"]
        # 模型偶尔用 ```json 包裹输出，剥掉外壳；strict=False 容忍句子里的换行符
        start, end = text.find("["), text.rfind("]")
        data = json.loads(text[start:end + 1], strict=False)
        out = [{"en": d["en"].strip(), "zh": d["zh"].strip()}
               for d in data if d.get("en") and d.get("zh")]
        return out[:n] or None
    except Exception:
        return None


def generate_dialogues(word: str, zh: str, api_key: str, n: int = 4):
    """调 AI 生成诊室对话；任何失败都返回 None，让调用方走兜底"""
    if not api_key:
        return None
    return _call(PROMPT.format(word=word, zh=zh), api_key, n)


def generate_scene(word: str, zh: str, scene: str, api_key: str, n: int = 3):
    """按用户给的中文场景提示生成定制对话，失败返回 None"""
    if not api_key or not scene.strip():
        return None
    return _call(SCENE_PROMPT.format(word=word, zh=zh, scene=scene.strip()),
                 api_key, n)

# -*- coding: utf-8 -*-
"""AI 生成诊室对话（智谱 glm-4-flash，免费模型）

根据单词的真实含义现编简短、地道的治疗师-患者对话，
配置 zhipu_api_key 后启用；没配置或调用失败时调用方会退回语料例句。
"""
import json

import requests

API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

# 提示词版本号：改了提示词就 +1，让应用端的缓存失效、重新生成
PROMPT_VERSION = 3

PROMPT = """你是美国社区诊所里一位说话随和的物理治疗师，正在和普通患者面对面聊天。\
请用英文单词「{word}」（中文含义：{zh}）写 2 句你在诊室里对患者说的日常口语。

患者是不懂医学的普通人，你说话必须像聊天一样简单自然。参考下面的风格：

✅ 好的例子（要这种口语感）：
- "Does it hurt right here?"
- "Keep your back straight, like this."
- "Your knee's looking way better."

❌ 坏的例子（太书面太学术，绝对禁止）：
- "It is important to maintain proper postural alignment."
- "This intervention will facilitate functional recovery."

硬性要求：
1. 每句自然用到 {word}（可用常见变形），语法正确
2. 每句 10 个词以内，越短越好，多用缩写（it's / let's / you're）
3. 两句场景不同：一句提问或动作指导，一句用大白话解释
4. 中文翻译也要口语化，像当面说话

只输出 JSON 数组，不要任何其他文字，格式：
[{{"en": "英文句子", "zh": "中文翻译"}}]"""


IMAGE_URL = "https://open.bigmodel.cn/api/paas/v4/images/generations"

IMAGE_PROMPT = ("简洁清晰的医学教学插画：{zh}（英文：{word}），物理治疗康复场景，"
                "柔和浅色背景，扁平简约风格，画面里不要出现任何文字")


def generate_image(word: str, zh: str, api_key: str):
    """用免费的 CogView-3-Flash 生成单词示意图，失败返回 None"""
    if not api_key:
        return None
    try:
        r = requests.post(
            IMAGE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "cogview-3-flash",
                "prompt": IMAGE_PROMPT.format(word=word, zh=zh),
                "size": "1024x1024",
            },
            timeout=60,
        )
        return r.json()["data"][0]["url"]
    except Exception:
        return None


def generate_dialogues(word: str, zh: str, api_key: str, n: int = 3):
    """调 AI 生成诊室对话；任何失败都返回 None，让调用方走兜底"""
    if not api_key:
        return None
    try:
        r = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "glm-4-flash",
                "messages": [
                    {"role": "user", "content": PROMPT.format(word=word, zh=zh)},
                ],
                "temperature": 0.7,
            },
            timeout=30,
        )
        text = r.json()["choices"][0]["message"]["content"]
        # 模型偶尔用 ```json 包裹输出，剥掉外壳再解析
        start, end = text.find("["), text.rfind("]")
        data = json.loads(text[start:end + 1])
        out = [{"en": d["en"].strip(), "zh": d["zh"].strip()}
               for d in data if d.get("en") and d.get("zh")]
        return out[:n] or None
    except Exception:
        return None

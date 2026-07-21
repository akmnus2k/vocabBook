# -*- coding: utf-8 -*-
"""AI 生成诊室对话（智谱 glm-4-flash，免费模型）

根据单词的真实含义现编简短、地道的治疗师-患者对话，
配置 zhipu_api_key 后启用；没配置或调用失败时调用方会退回语料例句。
"""
import json

import requests

API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

PROMPT = """你是一位在物理治疗（PT）诊所工作的英语老师。\
请用英文单词「{word}」（中文含义：{zh}）造 3 句物理治疗师日常工作中真实会说的话，\
比如问诊、体格检查、指导训练动作、向患者解释病情时的口语表达。

要求：
1. 每句必须自然地用到 {word}（可以是常见变形），语法必须正确
2. 句子简短口语化，15 个词以内，像治疗师和患者面对面交谈
3. 三句覆盖不同场景（比如一句问诊、一句指导、一句解释）
4. 每句配自然的中文翻译

只输出 JSON 数组，不要任何其他文字，格式：
[{{"en": "英文句子", "zh": "中文翻译"}}]"""


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

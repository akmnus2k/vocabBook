# -*- coding: utf-8 -*-
"""打印今天待复习的单词数量（给提醒脚本 remind.vbs 用）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import storage

book = storage.load_book()
print(len(storage.due_words(book)))

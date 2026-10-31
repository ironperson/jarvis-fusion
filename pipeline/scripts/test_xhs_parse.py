"""小红书解析器离线测试（无需浏览器/网络）。"""
import sys
sys.path.insert(0, ".")
from collectors.xiaohongshu import parse_note_page

# 用例1：完整 JSON（含嵌套花括号 + 中文 + #标签）
s1 = '''<html><head><meta property="og:title" content="meta标题兜底">
<meta property="og:description" content="meta描述兜底"></head><body>
<script>window.__INITIAL_STATE__={"noteData":{"noteData":{"abc123":{
  "title":"如何搭建个人知识库","desc":"第一步明确目标。#知识管理 #效率工具",
  "tagList":[{"name":"知识管理"},{"name":"效率工具"}],
  "user":{"nickname":"小贾"}}}}};</script></body></html>'''
r1 = parse_note_page(s1, "u")
assert r1["title"] == "如何搭建个人知识库", r1
assert r1["tags"] == ["知识管理", "效率工具"], r1
assert r1["author"] == "小贾", r1
print("用例1 完整JSON: ✅", r1["title"], r1["tags"], r1["author"])

# 用例2：JSON 缺字段 -> meta 兜底
s2 = '''<html><head><meta property="og:title" content="meta标题">
<meta property="og:description" content="meta正文"></head><body>
<script>window.__INITIAL_STATE__={"noteData":{"noteData":{"x":{}}};};</script></body></html>'''
r2 = parse_note_page(s2, "u")
assert r2["title"] == "meta标题", r2
assert r2["text"] == "meta正文", r2
print("用例2 JSON缺字段->meta兜底: ✅", r2["title"], r2["text"])

# 用例3：完全无 JSON -> 仅 meta
s3 = '''<html><head><meta property="og:title" content="纯meta标题">
<meta property="og:description" content="纯meta正文"></head><body><p>正文</p></body></html>'''
r3 = parse_note_page(s3, "u")
assert r3["title"] == "纯meta标题" and r3["text"] == "纯meta正文", r3
print("用例3 仅meta: ✅", r3["title"], r3["text"])

print("全部解析用例通过 ✅")

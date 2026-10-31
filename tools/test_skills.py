import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jarvis.skills import system as S

print("=== 1. system_status ===")
print(" ", S.system_status())
print()
print("=== 2. list_apps (top) ===")
print(" ", S.list_apps(12)[:600])
print()
print("=== 3. find_files: 找一个真实文件 ===")
print(" ", S.find_files("jarvis", limit=5)[:600])
print()
print("=== 4. screenshot ===")
print(" ", S.screenshot()[:200])
print()
print("=== 5. resolve_app 映射检查 ===")
for n in ["记事本", "计算器", "飞书", "浏览器"]:
    print(f"   {n} -> {S.resolve_app(n)}")

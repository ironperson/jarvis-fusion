"""M1 验证：用手造的原子笔记验证「入库 → 嵌入 → 检索」全链路。

这些样例笔记采用 PKM 结构（原子化、唯一 ID、PARA 分类、标签），
用于确认骨架可写、可嵌入、可语义检索。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models import Note, NoteType, ParaCategory
from ingest.ingest import ingest_note
from core import search as search_mod


def main() -> None:
    samples = [
        Note(
            note_id="n-zettelkasten", title="卡片盒笔记法的核心",
            content="Zettelkasten 主张每张笔记只装一个独立想法（原子化），并通过唯一 ID 与双向链接连成网络，而非按文件夹堆叠。",
            note_type=NoteType.PERMANENT, para=ParaCategory.RESOURCE, tags=["PKM", "方法论"],
        ),
        Note(
            note_id="n-para", title="PARA 四层分类",
            content="PARA 把信息分为 Project（有截止）/ Area（持续领域）/ Resource（参考）/ Archive（归档），用它决定一条笔记该放哪。",
            note_type=NoteType.PERMANENT, para=ParaCategory.RESOURCE, tags=["PARA", "组织"],
        ),
        Note(
            note_id="n-embedding", title="向量嵌入让知识可语义检索",
            content="把文本用 bge-m3 等嵌入模型转成向量后，可用余弦相似度做语义检索，不必依赖关键词完全匹配。",
            note_type=NoteType.LITERATURE, para=ParaCategory.RESOURCE, tags=["检索", "嵌入"],
        ),
        Note(
            note_id="n-xhs-collect", title="小红书采集的合规边界",
            content="自动化抓取小红书需登录态且有反爬限制；个人非商用、限频、不转售是收敛风险的底线。",
            note_type=NoteType.LITERATURE, para=ParaCategory.PROJECT, tags=["采集", "合规"],
            source="xiaohongshu",
        ),
    ]

    for s in samples:
        n = ingest_note(s)
        print(f"✓ 入库：{n.title}  ({n.note_id})")

    print("\n--- 语义检索测试：'如何组织我的笔记？' ---")
    for r in search_mod.search("如何组织我的笔记？", top_k=3):
        print(f"[{r['score']:.3f}] {r['title']}")


if __name__ == "__main__":
    main()

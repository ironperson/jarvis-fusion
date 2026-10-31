"""深色、信息密度优先的 Qt 样式。"""
from __future__ import annotations

DARK_QSS = """
QMainWindow, QWidget { background: #161b22; color: #dce4ee; font-family: "Microsoft YaHei UI"; font-size: 13px; }
QTextEdit, QPlainTextEdit, QTreeWidget { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 6px; selection-background-color: #264f78; }
QPushButton { background: #238636; border: 1px solid #2ea043; border-radius: 5px; padding: 7px 14px; color: white; }
QPushButton:hover { background: #2ea043; }
QPushButton:disabled { background: #30363d; border-color: #484f58; color: #8b949e; }
QPushButton#stopButton { background: #8b2635; border-color: #d24d57; }
QCheckBox { spacing: 6px; }
QLabel[active="true"] { color: #56d364; font-weight: 600; }
QLabel[active="false"] { color: #8b949e; }
QHeaderView::section { background: #21262d; color: #dce4ee; border: 0; padding: 6px; }
QSplitter::handle { background: #30363d; width: 2px; }
QToolTip { background: #21262d; color: #f0f6fc; border: 1px solid #58a6ff; padding: 5px; }
"""

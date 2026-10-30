"""兼容入口：实际程序在项目根目录的 word_study.py，避免两份代码行为不一致。"""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from word_study import WordStudyApp, main

if __name__ == "__main__":
    os.chdir(_ROOT)
    main()

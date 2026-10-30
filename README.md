# WordStudy · 英语单词学习终端

> 一个跑在 Windows 终端里的英语单词学习工具：**FSRS 间隔重复排程 + Edge TTS 美式发音 + 14 本主流词书 + 雅思阅读训练**。
> 全键盘操作、无图形界面依赖，背诵、复习、听力、拼写、阅读一个窗口搞定。

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-blue">
  <img alt="Platform" src="https://img.shields.io/badge/Platform-Windows-0078D6">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-green">
  <img alt="Scheduler" src="https://img.shields.io/badge/FSRS-6-orange">
</p>

**简体中文** | [English](README.en.md)

---

## 🖼️ 界面预览

<p align="center">
  <img src="docs/screenshot-menu.png" width="600" alt="WordStudy 主菜单">
  <br>
  <img src="docs/screenshot-quiz.png" width="600" alt="WordStudy 看英选中答题界面">
</p>

---

## ✨ 功能一览

### 六种学习方式

| 模式 | 做法 | 适合 |
| --- | --- | --- |
| 👂 看英选中 | 看单词 + 听发音 → 从 4 个中文释义里选 | 学新词 |
| 📝 看中选英 | 看中文释义 → 从 4 个英文单词里选 | 日常复习 |
| ✏️ 看中拼写英 | 看中文 → 逐字符敲出英文拼写 | 强化拼写 |
| 🎧 听音选中 | 只听发音 → 选中文释义 | 听力训练 |
| 🎯 自由练习 | 随机抽词练，**不写入复习计划** | 顽固词突击 |
| 🌱 自由学习 | 只判「认识 / 不认识」，不认识的看释义 + 例句后重新排队，直到全部认识 | 快速过词 |

选择题会自动打乱正确项位置；答错立刻公布答案，并按对错与答题速度自动折算成 FSRS 评分（无需手动选择难度）。

### 🧠 FSRS 智能复习
- 基于 [py-fsrs](https://github.com/open-spaced-repetition/py-fsrs) 的稳定性 / 难度模型，而非固定间隔表
- **到期复习优先**，再按每日额度引入新词；两者不足时自动补足「今日已学 + 记忆薄弱」的巩固词
- 答错 → `Again` 立即重排；答对但超时或重听 → `Hard`；正常答对 → `Good`
- 新词首次答对固定次日再见，第二次起完全交给 FSRS
- 目标记忆保持率可调（默认 **0.90**），最大间隔 3650 天，顽固词自动标记
- 每日新词上限（默认 15）与每次学习数量（默认按配置）均可调

### 📚 内置词库
`word_learn.db` 随仓库提供，开箱即用：

| 类别 | 词书 |
| --- | --- |
| 考研 | 2024 / 2025 / 2026 考研词汇、红宝书、大纲 5530、核心 2700 |
| 四六级 | 四级大纲 5278、六级大纲 5278、四级 / 六级核心 2700 |
| 雅思 | 新东方雅思 7000 |
| 高考 / 中考 | 高考 3500、中考核心词汇 |
| 通用 | Oxford 3000 / 5000、COCA 20000 高频词 |

共 **14 本词书、20,780 个词条、7.2 万条词书关联**。支持中英文模糊搜索词书、按级别（小学 → 雅思）筛选、分页浏览，并自动记住上次选中的词书。

### 🔊 发音与听力
- 使用 **Edge TTS** 在线合成美式发音（`en-US-AriaNeural` / `en-US-GuyNeural`）
- 合成结果按 `语音|语速|文本` 缓存到 `data/tts_cache/`，同一个词只合成一次，断网也能复播
- 支持语速调节、例句朗读、今日单词循环播放，以及内置的英文小故事（《小王子》《三只小猪》等）

### 📖 雅思阅读训练
- 真题式训练流程：选套题 → 分栏阅读原文 → 逐题作答 → 判分 → 错题回顾
- 终端阅读器左侧原文、右侧释义栏，**鼠标双击单词即查释义**
- 覆盖单选、多选、T/F/NG、Y/N/NG、匹配、分类、摘要填空、句子/表格/图示填空、简答等题型
- 题库不随仓库分发，运行 `python import_reading.py` 从 [hwttop5/ielts-reading-past-papers](https://github.com/hwttop5/ielts-reading-past-papers) 下载并导入 200+ 篇、约 2800 题

### 📓 错题本
答错的词自动进错题本，可按「今日 / 全部」查看，也可以只针对错题重练，练完仍按 FSRS 更新。

### 🔍 找词与导入
- 精准搜索：精确匹配优先，其次前缀匹配，不再把「包含关键词」的词全部倒出来
- 多选添加（如 `1,3,5`），一键加入学习列表
- **PDF 词表导入**：直接解析 PDF 里的英文 + 中文释义，覆盖本地词库后批量加入学习列表，自动跳过页码、音标和页眉页脚

### 📊 进度与配置
主菜单直接显示学习列表总数、今日到期、今日新词进度、待学新词、今日巩固词数；每次学习数量、每日新词上限、目标保持率、TTS 语速都在「9. 其他设置」里改，写入 `user_config.json`。

---

## 🚀 快速开始

**环境要求**：Windows 10/11、Python 3.10+、可联网（首次合成语音、导入阅读题库）

```bash
git clone https://github.com/StarShine761/WordStudy.git
cd WordStudy
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动方式（任选其一）：

```bash
start.bat                  # 推荐：自动设置 UTF-8、彩色输出，并把窗口调到 72×34 居中
.\.venv\Scripts\python.exe word_study.py
```

首次进入程序：

1. 选 `7` 搜索并添加单词（可先选词书，程序会记住你的选择）
2. 选 `1` 开始学新词，答错的词会自动进错题本
3. 之后每天打开程序，按主菜单提示的「今日到期」复习即可

主菜单顶部实时显示学习列表总数、今日到期、今日新词进度和待学新词；各选项含义见上方「功能一览」，运行效果见「界面预览」。

---

## 📂 项目结构

```
WordStudy/
├─ word_study.py         # 主程序：菜单、学习模式、发音、错题本、设置
├─ srs/                  # FSRS 排程核心
│  ├─ scheduler.py       #   评分映射 + 排程引擎
│  ├─ queue.py           #   到期 / 新词 / 巩固词的会话组装
│  ├─ quiz.py            #   干扰项生成
│  ├─ store.py           #   学习记录读写
│  └─ schema.py          #   数据库表结构
├─ wordstudy/            # 可安装的包入口（兼容旧引用）
├─ reading_viewer.py     # 终端阅读器：左右分栏、鼠标双击查词
├─ reading_exam.py       # 答题界面：单页滚动、作答与判分
├─ reading_practice.py   # 套题选择、阅读会话、错题记录
├─ import_reading.py     # 下载并导入雅思阅读题库
├─ rebuild_vocab_db.py   # 从开源词表重建 word_learn.db
├─ meaning_format.py     # 释义按词性拆行排版
├─ word_learn.db         # 词库：14 本词书 / 20,780 词
├─ requirements.txt
├─ start.bat / start.ps1
└─ test_*.py             # 单元测试
```

## 💾 数据文件

| 文件 | 是否入库 | 说明 |
| --- | --- | --- |
| `word_learn.db` | ✅ | 词库，随仓库提供 |
| `word_study.db` | ❌ | 你的学习记录：学习列表、FSRS 状态、错题本、阅读题库、作答日志 |
| `user_config.json` | ❌ | 个人设置：每次学习数量、语速等 |
| `data/` | ❌ | 备份、原始词表、TTS 缓存、阅读题库源文件 |

`word_study.db`、`user_config.json`、`data/` 已写入 `.gitignore`，学习进度不会被提交。想从头开始，删掉 `word_study.db` 即可。

## 🔧 词库来源与重建

`word_learn.db` 由 `rebuild_vocab_db.py` 从以下开源词表生成：

- [exam-data/NETEMVocabulary](https://github.com/exam-data/NETEMVocabulary)、[exam-data/CETVocabulary](https://github.com/exam-data/CETVocabulary) — 考研 / 四六级大纲词汇
- [RealKai42/qwerty-learner](https://github.com/RealKai42/qwerty-learner) — 红宝书、雅思、牛津、COCA 等词书
- [skywind3000/ECDICT](https://github.com/skywind3000/ECDICT) — 音标与释义补全

```bash
python rebuild_vocab_db.py      # 重建词库（旧库自动备份到 data/backup/）
python import_reading.py        # 下载并导入雅思阅读题库
```

词条内容的版权归各词表 / 词书原作者所有，本仓库仅做格式整合，仅供个人学习使用。

## ❓ 常见问题

**发音没有声音？**
`edge-tts` 首次合成需要联网；确认系统默认音频设备正常。合成过的词会缓存，之后可离线播放。

**中文或表情乱码？**
请用 `start.bat` 启动（自动切 UTF-8 + VT 色彩），或在 PowerShell 里先执行 `chcp 65001`。

**提示找不到 `word_learn.db`？**
确认克隆完整（该文件约 15 MB），或运行 `python rebuild_vocab_db.py` 重新生成。

**支持 macOS / Linux 吗？**
不支持。程序依赖 Windows 控制台 API（`ctypes` + `winmm`）实现居中排版、鼠标查词和音频播放。

**如何加更多词书？**
把词表整理成 `data/wordlists/` 下的 JSON，或改 `rebuild_vocab_db.py` 的源列表后重建；也可以在程序里用「7.2 从 PDF 导入单词」临时补充。

**要学的单词词库里没有怎么办？**
不用手工改数据库：在「7.1 直接搜索单词」里输入该词，或在「7.2 从 PDF 导入单词」的词表中带上它，程序会自动把它新增到本地词库并加入学习列表。

## 🧪 测试

```bash
.\.venv\Scripts\python.exe -m unittest test_choice_prompt.py test_meaning_format.py
```

## 📄 License

代码以 [MIT License](LICENSE) 发布。词库与雅思阅读题库内容版权归原始来源所有。

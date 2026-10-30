#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
英语单词学习工具 - CLI版本
支持FSRS复习算法、发音、多种学习模式
"""

import sqlite3
import json
import os
import re
import sys
import io
import time
import builtins
import hashlib
import ctypes
import shutil
import threading
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import List
import platform
import random
import asyncio
import edge_tts

from srs import store as srs_store
from srs.schema import ensure_schema
from srs.scheduler import FSRSEngine, choose_rating
from srs.queue import build_session, session_stats
from srs.quiz import build_options
from meaning_format import format_meaning, format_option, meaning_lines

# Enable Windows console VT (Virtual Terminal) processing so ANSI color codes
# (e.g. \033[1;32m) render as colors instead of showing as raw text.
# This MUST be done inside the Python process: each process has its own stdout
# console handle, so enabling VT in the parent PowerShell does NOT affect Python output.
if sys.platform == 'win32':
    try:
        import ctypes
        _k = ctypes.windll.kernel32
        _k.GetStdHandle.restype = ctypes.c_void_p
        _h = _k.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        _mode = ctypes.c_uint()
        if _k.GetConsoleMode(_h, ctypes.byref(_mode)):
            _k.SetConsoleMode(_h, _mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass

# Windows 原生音频播放（MCI/DirectShow）：避免 PortAudio 在蓝牙设备上
# 走 MME 共享模式时的重采样与抖动，那正是随机卡顿和电流声的来源
if sys.platform == 'win32':
    _winmm = ctypes.WinDLL('winmm')
    _winmm.mciSendStringW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p
    ]
    _winmm.mciSendStringW.restype = ctypes.c_uint
    _winmm.mciGetErrorStringW.argtypes = [
        ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint
    ]
    _winmm.mciGetErrorStringW.restype = ctypes.c_bool
    _winmm.waveOutGetNumDevs.restype = ctypes.c_uint


def _mci_send(command: str) -> str:
    """执行一条 MCI 命令，失败时抛出带系统错误描述的 OSError"""
    if sys.platform != 'win32':
        raise RuntimeError("音频播放仅支持 Windows")
    buf = ctypes.create_unicode_buffer(256)
    err = _winmm.mciSendStringW(command, buf, 256, None)
    if err:
        msg = ctypes.create_unicode_buffer(256)
        _winmm.mciGetErrorStringW(err, msg, 256)
        raise OSError(f"{msg.value}（{command}）")
    return buf.value


_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
_BLOCK_COLUMNS = 60


def _char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1


def _display_width(text: str) -> int:
    """终端显示宽度：中文/emoji 按 2 列计算，忽略颜色转义"""
    text = _ANSI_ESCAPE_RE.sub('', text)
    return sum(_char_width(ch) for ch in text)


def _wrap_display(text: str, width: int):
    """按显示宽度折行，优先在空格处断开，并保留首行缩进（ANSI 颜色不参与宽度）"""
    if _display_width(text) <= width:
        return [text]
    indent = text[:len(text) - len(text.lstrip(' '))]
    body = text[len(indent):]
    indent_width = _display_width(indent)
    if indent_width >= width - 10:
        indent, indent_width = '', 0
    avail = width - indent_width

    tokens = []
    index = 0
    while index < len(body):
        if body[index] == '\x1b':
            match = _ANSI_ESCAPE_RE.match(body, index)
            if match:
                tokens.append((match.group(0), 0))
                index = match.end()
                continue
        tokens.append((body[index], _char_width(body[index])))
        index += 1

    lines = []
    current = []
    current_width = 0
    last_space = -1
    has_wide = any(token_width == 2 for _, token_width in tokens)
    for token, token_width in tokens:
        if token_width and current_width + token_width > avail:
            if not has_wide and last_space >= 0:
                head = ''.join(part for part, _ in current[:last_space]).rstrip()
                current = current[last_space + 1:]
                current_width = sum(part_width for _, part_width in current)
            else:
                head = ''.join(part for part, _ in current).rstrip()
                current = []
                current_width = 0
            if head.strip():
                lines.append(indent + head)
            last_space = -1
        current.append((token, token_width))
        current_width += token_width
        if token == ' ':
            last_space = len(current) - 1
    tail = ''.join(part for part, _ in current).rstrip()
    if tail.strip():
        lines.append(indent + tail)
    return lines or [text]


class CenteredStdout:
    """把界面限制在居中的 60 列区域（IDE 终端很宽时不再贴在左边）"""

    def __init__(self, stream):
        self._stream = stream
        self._buffer = ''
        self._lock = threading.Lock()
        self.enabled = True

    def set_enabled(self, enabled: bool):
        with self._lock:
            if not enabled and self._buffer:
                self._write_line(self._buffer)
                self._buffer = ''
            self.enabled = bool(enabled)

    def _write_line(self, line: str, center: bool = False):
        width = _display_width(line)
        if width <= 0:
            self._stream.write(line)
            return
        columns = shutil.get_terminal_size(fallback=(80, 24)).columns
        if center:
            pad = max(0, (columns - width) // 2)
            self._stream.write(' ' * pad + line)
            return
        pad = max(0, (columns - _BLOCK_COLUMNS) // 2)
        chunks = _wrap_display(line, _BLOCK_COLUMNS) if width > _BLOCK_COLUMNS else [line]
        for index, chunk in enumerate(chunks):
            if index:
                self._stream.write('\n')
            self._stream.write(' ' * pad + chunk)

    def write(self, text):
        with self._lock:
            if not self.enabled:
                return self._stream.write(text)
            self._buffer += text
            while True:
                idx_n = self._buffer.find('\n')
                idx_r = self._buffer.find('\r')
                if idx_n == -1 and idx_r == -1:
                    break
                if idx_r == -1 or (idx_n != -1 and idx_n < idx_r):
                    idx, sep = idx_n, '\n'
                else:
                    idx, sep = idx_r, '\r'
                line = self._buffer[:idx]
                self._buffer = self._buffer[idx + 1:]
                self._write_line(line)
                self._stream.write(sep)
            return len(text)

    def flush(self):
        with self._lock:
            if self.enabled and self._buffer:
                # 缓冲区里剩下的通常是不带换行的 input 提示，单独居中
                self._write_line(self._buffer, center=True)
                self._buffer = ''
            self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install_centered_output():
    """交互终端下启用居中输出；管道/重定向时保持原样"""
    if isinstance(sys.stdout, CenteredStdout) or not sys.stdout.isatty():
        return
    sys.stdout = CenteredStdout(sys.stdout)
    _install_centered_input()


def _install_centered_input():
    """真实终端下 input() 的提示不走 sys.stdout，改为自己居中打印提示"""
    if getattr(_install_centered_input, "_installed", False):
        return
    _install_centered_input._installed = True
    original_input = builtins.input

    def centered_input(prompt=""):
        if prompt:
            sys.stdout.write(prompt)
            sys.stdout.flush()
        return original_input()

    builtins.input = centered_input


class WordStudyApp:
    def __init__(self, db_path: str = "word_learn.db"):
        self.db_path = db_path
        self.user_id = 1  # 默认用户ID
        self.study_db_path = "word_study.db"
        self.config_path = "user_config.json"
        self.study_count = 30  # 默认每次学习单词数量
        self.init_tts()
        self.init_study_db()
        self.load_study_count()  # 加载学习数量设置
        self.init_srs()

    def _load_config(self) -> dict:
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_config(self, updates: dict):
        config = self._load_config()
        config.update(updates)
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ 保存设置失败: {e}")

    def load_study_count(self):
        """加载每次学习单词数量"""
        config = self._load_config()
        try:
            self.study_count = max(1, int(config.get('study_count', 30)))
        except (TypeError, ValueError):
            self.study_count = 30

    def save_study_count(self):
        self._save_config({'study_count': self.study_count})

    def init_srs(self):
        """初始化 FSRS 调度器及学习计划参数"""
        config = self._load_config()
        try:
            self.new_per_day = max(0, int(config.get('new_per_day', 15)))
        except (TypeError, ValueError):
            self.new_per_day = 15
        try:
            self.desired_retention = min(
                0.97, max(0.70, float(config.get('desired_retention', 0.9)))
            )
        except (TypeError, ValueError):
            self.desired_retention = 0.9
        try:
            self.maximum_interval = max(30, int(config.get('maximum_interval', 3650)))
        except (TypeError, ValueError):
            self.maximum_interval = 3650
        try:
            self.slow_answer_seconds = max(
                3.0, float(config.get('slow_answer_seconds', 10))
            )
        except (TypeError, ValueError):
            self.slow_answer_seconds = 10.0
        self.leech_threshold = 4
        self._build_engine()

    def _build_engine(self):
        self.fsrs = FSRSEngine(
            desired_retention=self.desired_retention,
            maximum_interval=self.maximum_interval,
        )
    
    def init_tts(self):
        """初始化 Edge TTS 引擎（美式英语发音）"""
        # en-US-AriaNeural: 清晰女声，适合学习；en-US-GuyNeural: 标准男声
        self.tts_voice_male = "en-US-GuyNeural"
        self.tts_voice_female = "en-US-AriaNeural"
        self.tts_voice = self.tts_voice_female
        self.tts_use_female = True

        self.tts_cache_dir = Path("data") / "tts_cache"
        self.tts_cache_dir.mkdir(parents=True, exist_ok=True)
        self._play_lock = threading.Lock()
        self._tts_state_lock = threading.Lock()
        self._speak_seq = 0
        self._tts_warned = set()

        # 从配置文件读取保存的语速，默认1.0x（正常速度）
        self.tts_speed = 1.0
        self.tts_rate = "+0%"
        try:
            config = {}
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            self.tts_speed = config.get('tts_speed', 1.0)
            percent = round((self.tts_speed - 1.0) * 100)
            self.tts_rate = f"{percent:+d}%"
            print(f"✅ Edge TTS 初始化成功（语速: {self.tts_speed}x）")
        except Exception as e:
            print(f"⚠️ Edge TTS 初始化失败: {e}")

        self._check_output_device()

    def _check_output_device(self):
        """启动时确认存在可用的播放设备，避免无声且无提示"""
        if sys.platform != 'win32':
            self.tts_voice = None
            print("⚠️ 音频播放仅支持 Windows（当前系统无法发音）")
            return
        try:
            if _winmm.waveOutGetNumDevs() == 0:
                self._warn_tts(
                    "output",
                    "⚠️ 未检测到可用的音频输出设备（耳机/音箱未连接或被禁用），"
                    "发音将没有声音；接上设备后重启本程序"
                )
        except Exception:
            pass

    def _warn_tts(self, key: str, message: str):
        """同一类问题只提示一次，避免刷屏"""
        if key in self._tts_warned:
            return
        self._tts_warned.add(key)
        print(message)
    
    def init_study_db(self):
        """初始化学习数据库（自动迁移到 FSRS 表结构）"""
        ensure_schema(self.study_db_path)

        conn = sqlite3.connect(self.study_db_path)
        cursor = conn.cursor()

        # 例句表（独立表，不与其他表关联）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS phrases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                english TEXT,
                chinese TEXT,
                add_date TEXT
            )
        ''')
        
        # 错题本表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wrong_words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                voc_id TEXT,
                vocabulary TEXT,
                interpretation TEXT,
                phonetic_us TEXT,
                add_date TEXT,
                wrong_count INTEGER DEFAULT 1,
                last_wrong_date TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def _next_tts_request(self):
        """取本次发音的语音并切换到下一次的男女声"""
        with self._tts_state_lock:
            self._speak_seq += 1
            seq = self._speak_seq
            voice = self.tts_voice
            if self.tts_use_female:
                self.tts_voice = self.tts_voice_male
            else:
                self.tts_voice = self.tts_voice_female
            self.tts_use_female = not self.tts_use_female
        return seq, voice

    def _cache_path(self, text: str, voice: str) -> Path:
        key = f"{voice}|{self.tts_rate}|{text}"
        digest = hashlib.sha1(key.encode('utf-8')).hexdigest()
        return self.tts_cache_dir / f"{digest}.mp3"

    def _ensure_cached(self, text: str, voice: str) -> Path:
        """返回音频缓存路径，没有则合成并落盘（同一文本只合成一次）"""
        path = self._cache_path(text, voice)
        if path.exists() and path.stat().st_size > 0:
            return path
        self._synthesize(text, voice, path)
        return path

    def _synthesize(self, text: str, voice: str, path: Path):
        async def collect():
            communicate = edge_tts.Communicate(text, voice, rate=self.tts_rate)
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()

        data = asyncio.run(collect())
        if not data:
            raise RuntimeError("未收到音频数据")
        # 先写临时文件再原子替换，避免并发读到半截文件
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def _fade_out(self, alias: str):
        """中断播放时先把音量快速降到 0，避免硬切波形产生爆音"""
        for volume in range(1000, -1, -250):
            try:
                _mci_send(f'setaudio {alias} volume to {volume}')
            except OSError:
                return
            time.sleep(0.012)

    def _play_cached(self, path: Path, seq: int, interruptible: bool = True):
        alias = f"tts{os.getpid()}_{seq}"
        with self._play_lock:
            # 连按跳过时，已有更新的请求在等，就不播这条旧音频
            if interruptible and seq != self._speak_seq:
                return
            _mci_send(f'open "{path}" type mpegvideo alias {alias}')
            try:
                _mci_send(f'play {alias}')
                try:
                    length_ms = int(_mci_send(f'status {alias} length') or 0)
                except (OSError, ValueError):
                    length_ms = 0
                deadline = time.monotonic() + (length_ms / 1000 if length_ms else 30) + 3
                started = False
                idle = 0
                while time.monotonic() < deadline:
                    if interruptible and seq != self._speak_seq:
                        self._fade_out(alias)
                        return
                    mode = _mci_send(f'status {alias} mode')
                    if mode == 'playing':
                        started = True
                        idle = 0
                    else:
                        idle += 1
                        # 播放已结束，或 200ms 内始终没进入 playing（极短音频）
                        if started or idle >= 4:
                            break
                    time.sleep(0.05)
            finally:
                try:
                    _mci_send(f'close {alias}')
                except OSError:
                    pass

    def speak(self, text: str):
        """发音（后台合成+播放，男女声交替，相同文本只合成一次）"""
        if not self.tts_voice or not text or not text.strip():
            return
        text = text.strip()
        seq, voice = self._next_tts_request()

        def _run():
            try:
                path = self._ensure_cached(text, voice)
            except Exception as e:
                self._warn_tts("synth", f"⚠️ 发音合成失败（检查网络或升级 edge-tts）: {e}")
                return
            try:
                self._play_cached(path, seq)
            except Exception as e:
                self._warn_tts("play", f"⚠️ 音频播放失败: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def speak_sync(self, text: str):
        """同步发音（等待播放完成，男女声交替）"""
        if not self.tts_voice or not text or not text.strip():
            return
        text = text.strip()
        seq, voice = self._next_tts_request()
        try:
            path = self._ensure_cached(text, voice)
            self._play_cached(path, seq, interruptible=False)
        except Exception as e:
            self._warn_tts("play", f"⚠️ 音频播放失败: {e}")
    
    def search_words(self, keyword: str, limit: int = 20) -> List[Dict]:
        """智能搜索单词：优先全匹配，然后前缀匹配，最后模糊匹配"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        keyword_lower = keyword.lower()
        
        words = []
        seen = set()
        
        # 优先级1: 全匹配（完全相等）
        cursor.execute('''
            SELECT v.vc_id, v.vc_vocabulary, v.vc_interpretation, v.vc_phonetic_us
            FROM VOC_TB v
            WHERE LOWER(v.vc_vocabulary) = ?
        ''', (keyword_lower,))
        
        for row in cursor.fetchall():
            word_key = row[0]
            if word_key not in seen:
                seen.add(word_key)
                words.append({
                    'vc_id': row[0],
                    'vocabulary': row[1],
                    'interpretation': row[2],
                    'phonetic_us': row[3]
                })
        
        # 优先级2: 前缀匹配（以关键词开头）
        if len(words) < limit:
            remaining = limit - len(words)
            cursor.execute('''
                SELECT v.vc_id, v.vc_vocabulary, v.vc_interpretation, v.vc_phonetic_us
                FROM VOC_TB v
                WHERE LOWER(v.vc_vocabulary) LIKE ? AND LOWER(v.vc_vocabulary) != ?
                LIMIT ?
            ''', (f'{keyword_lower}%', keyword_lower, remaining))
            
            for row in cursor.fetchall():
                word_key = row[0]
                if word_key not in seen:
                    seen.add(word_key)
                    words.append({
                        'vc_id': row[0],
                        'vocabulary': row[1],
                        'interpretation': row[2],
                        'phonetic_us': row[3]
                    })
        
        # 优先级3: 模糊匹配（包含关键词）
        if len(words) < limit:
            remaining = limit - len(words)
            cursor.execute('''
                SELECT v.vc_id, v.vc_vocabulary, v.vc_interpretation, v.vc_phonetic_us
                FROM VOC_TB v
                WHERE LOWER(v.vc_vocabulary) LIKE ? 
                AND LOWER(v.vc_vocabulary) != ?
                AND LOWER(v.vc_vocabulary) NOT LIKE ?
                LIMIT ?
            ''', (f'%{keyword_lower}%', keyword_lower, f'{keyword_lower}%', remaining))
            
            for row in cursor.fetchall():
                word_key = row[0]
                if word_key not in seen:
                    seen.add(word_key)
                    words.append({
                        'vc_id': row[0],
                        'vocabulary': row[1],
                        'interpretation': row[2],
                        'phonetic_us': row[3]
                    })
        
        conn.close()
        return words
    
    def add_to_study_list(self, words: List[Dict]) -> int:
        """添加单词到学习列表（新卡片 state=0，学习时计入每日新词额度）"""
        now = int(time.time())
        today = datetime.now().strftime('%Y%m%d')
        conn = sqlite3.connect(self.study_db_path)
        try:
            added = 0
            for word in words:
                voc_id = word.get('vc_id') or word.get('voc_id')
                if not voc_id:
                    continue
                added += srs_store.add_card(
                    conn, str(voc_id), word.get('vocabulary', ''), now, today
                )
            conn.commit()
        finally:
            conn.close()
        return added

    def get_today_study_words(self, max_count: int = None) -> List[Dict]:
        """获取本次学习队列（到期复习优先，新词受每日额度限制）"""
        return self.get_session_words(max_count)

    def get_today_review_words(self, max_count: int = None) -> List[Dict]:
        return self.get_session_words(max_count)

    def get_session_words(self, max_count: int = None) -> List[Dict]:
        size = self.study_count if max_count is None else max_count
        now = int(time.time())
        today = datetime.now().strftime('%Y%m%d')
        conn = sqlite3.connect(self.study_db_path)
        try:
            plan = build_session(conn, now, today, size, self.new_per_day)
        finally:
            conn.close()
        return self._cards_to_words(plan.items)

    def _cards_to_words(self, items) -> List[Dict]:
        if not items:
            return []
        enriched = self.enrich_words(
            [(item.card.voc_id, item.card.vocabulary) for item in items]
        )
        flags = {str(item.card.voc_id): item.is_new for item in items}
        extra_flags = {str(item.card.voc_id): item.is_extra for item in items}
        for word in enriched:
            word['voc_id'] = word['vc_id']
            word['is_new'] = flags.get(str(word['vc_id']), False)
            word['is_extra'] = extra_flags.get(str(word['vc_id']), False)
        return enriched

    def get_practice_words(self, max_count: int = None) -> List[Dict]:
        """自由练习：从学习列表随机抽取，不限制到期状态"""
        size = self.study_count if max_count is None else max_count
        conn = sqlite3.connect(self.study_db_path)
        try:
            rows = conn.execute(
                "SELECT voc_id, vocabulary FROM study_list ORDER BY RANDOM() LIMIT ?",
                (size,),
            ).fetchall()
        finally:
            conn.close()
        words = self.enrich_words(rows)
        for word in words:
            word['voc_id'] = word['vc_id']
            word['is_new'] = False
        return words

    def get_session_stats(self) -> dict:
        now = int(time.time())
        today = datetime.now().strftime('%Y%m%d')
        conn = sqlite3.connect(self.study_db_path)
        try:
            return session_stats(conn, now, today, self.new_per_day, self.study_count)
        finally:
            conn.close()

    def _print_empty_session_hint(self, action: str = "学习") -> int:
        """打印无词提示，返回学习列表总数"""
        total = self.get_session_stats()['total']
        if total == 0:
            print("❌ 学习列表还是空的，暂时没有单词可学")
            print("💡 先用「7. 搜索并添加单词」把想学的词加入学习列表")
            return 0
        print(f"❌ 今天没有到期单词，也没有可学的新词（待{action}）")
        print("💡 到期复习会按 FSRS 计划出现；现在可以用「10. 自由练习」巩固已学单词，"
              "或用「7. 搜索并添加单词」添加新词")
        return total

    def _fallback_practice(self, action: str = "学习"):
        """今日无到期/新词时，询问是否改用自由练习；返回 (words, is_practice)"""
        total = self._print_empty_session_hint(action)
        if total == 0:
            choice = input("\n现在去搜索并添加单词？(y/n): ").strip().lower()
            if choice != 'y':
                return [], False
            self.interactive_search()
            words = self.get_today_study_words(self.study_count)
            if words:
                print("\n✅ 已把新词加入本次学习队列")
                return words, False
            return [], False
        choice = input(
            f"\n学习列表里有 {total} 个单词。是否用它们做自由练习？(y/n): "
        ).strip().lower()
        if choice != 'y':
            return [], False
        words = self.get_practice_words(self.study_count)
        if not words:
            print("❌ 学习列表里没有可用单词")
            return [], False
        print("\n🎯 已进入自由练习（本次作答不影响复习计划）")
        return words, True

    def is_card_due(self, voc_id: str) -> bool:
        conn = sqlite3.connect(self.study_db_path)
        try:
            return srs_store.is_due(conn, str(voc_id), int(time.time()))
        finally:
            conn.close()

    def enrich_words(self, words_data) -> List[Dict]:
        """批量丰富单词信息，保持传入顺序"""
        ids = []
        for item in words_data:
            if isinstance(item, (tuple, list)):
                voc_id = item[0]
            else:
                voc_id = item.get('vc_id') or item.get('voc_id')
            if voc_id:
                ids.append(str(voc_id))
        if not ids:
            return []

        unique_ids = list(dict.fromkeys(ids))
        by_id = {}
        conn = sqlite3.connect(self.db_path)
        try:
            for start in range(0, len(unique_ids), 500):
                chunk = unique_ids[start:start + 500]
                placeholders = ','.join('?' * len(chunk))
                cursor = conn.execute(
                    f'''
                    SELECT vc_id, vc_vocabulary, vc_interpretation,
                           vc_phonetic_us, vc_phonetic_uk, vc_difficulty
                    FROM VOC_TB WHERE vc_id IN ({placeholders})
                    ''',
                    chunk,
                )
                for row in cursor.fetchall():
                    by_id[str(row[0])] = row
        finally:
            conn.close()

        enriched = []
        for voc_id in ids:
            row = by_id.get(voc_id)
            if not row:
                continue
            enriched.append({
                'vc_id': row[0],
                'vocabulary': row[1],
                'interpretation': row[2],
                'phonetic_us': row[3],
                'phonetic_uk': row[4],
                'difficulty': row[5],
            })
        return enriched

    def build_quiz_options(self, word: Dict, kind: str = 'interpretation',
                           include_correct: bool = True) -> List[Dict]:
        """生成三个正常选项；include_correct=False 时全为干扰项"""
        conn = sqlite3.connect(self.db_path)
        try:
            return build_options(conn, word, kind, include_correct=include_correct)
        finally:
            conn.close()

    def _normalize_word(self, word: Dict) -> Dict:
        voc_id = str(word.get('vc_id') or word.get('voc_id') or '')
        return {
            'vc_id': voc_id,
            'voc_id': voc_id,
            'vocabulary': word.get('vocabulary', ''),
            'interpretation': word.get('interpretation', ''),
            'phonetic_us': word.get('phonetic_us', ''),
            'phonetic_uk': word.get('phonetic_uk', ''),
            'difficulty': word.get('difficulty'),
            'wrong_count': word.get('wrong_count'),
        }

    def record_review(self, word: Dict, correct: bool, duration_ms: int,
                      hint: bool = False, practice: bool = False):
        """记录一次作答；practice=True 时只写日志，不改动复习计划"""
        voc_id = str(word.get('vc_id') or word.get('voc_id') or '')
        if not voc_id:
            return None
        now = int(time.time())
        today = datetime.now().strftime('%Y%m%d')
        slow_ms = int(self.slow_answer_seconds * 1000)
        rating = int(choose_rating(correct, duration_ms, slow_ms, hint))
        result = None
        was_leech = False
        conn = sqlite3.connect(self.study_db_path)
        try:
            card = srs_store.fetch_card(conn, voc_id)
            if card is None and not practice:
                srs_store.add_card(
                    conn, voc_id, word.get('vocabulary', ''), now, today
                )
                card = srs_store.fetch_card(conn, voc_id)
            if card is None:
                # 不在学习列表里的单词只写练习日志，不自动加入复习计划
                srs_store.log_review(
                    conn, voc_id, rating, now, today, 0, None, None, 0, 0,
                    duration_ms, is_practice=1,
                )
                conn.commit()
            else:
                was_leech = bool(card.is_leech)
                if practice:
                    srs_store.log_review(
                        conn, voc_id, rating, now, today, card.state,
                        card.stability, card.difficulty, 0, 0,
                        duration_ms, is_practice=1,
                    )
                else:
                    updated, _ = self.fsrs.review(
                        card, rating, now_epoch=now, duration_ms=duration_ms
                    )
                    result = srs_store.apply_review(
                        conn, card, updated.state.value, updated.step,
                        updated.stability, updated.difficulty,
                        int(updated.due.timestamp()), rating, now, today,
                        duration_ms, self.leech_threshold,
                    )
                conn.commit()
        finally:
            conn.close()

        if not correct:
            self.add_to_wrong_words(voc_id)
        if result and result.get('is_leech') and not was_leech:
            print("⚠️ 该词已标记为顽固单词，建议用「自由练习」反复巩固")
        return result

    def _ask_quiz(self, word: Dict, kind: str = 'interpretation',
                  allow_replay: bool = False):
        """正确项在 1-4 中随机（4=以上三个都不对），5 恒判错；返回 None 表示用户选择返回"""
        correct_slot = random.randint(1, 4)
        options = self.build_quiz_options(
            word, kind, include_correct=correct_slot != 4
        )
        labels = '123'[:len(options)]
        print()
        show_meaning = kind == 'interpretation'
        correct_label = None
        correct_text = ''
        correct_detail = ''
        for label, option in zip(labels, options):
            text = (
                option['interpretation']
                if show_meaning else option['vocabulary']
            )
            if show_meaning:
                print(format_option(label, text, indent="  "))
            else:
                print(f"  {label}. {text}")
            print()
            if option['is_correct']:
                correct_label = label
                correct_text = text
        print("  4. 以上三个都不对")
        print()
        print("  5. 不确定")
        print()
        if correct_slot == 4:
            detail_label = (
                '正确释义' if show_meaning else '正确单词'
            )
            detail = (
                word.get('interpretation', '')
                if show_meaning else word.get('vocabulary', '')
            )
            correct_detail = detail
            correct_label = '4'
            correct_text = f'以上三个都不对（{detail_label}: {detail}）'
        elif correct_label is None:
            # 防御：正确答案必定在选项里，这里只兜底
            fallback = (
                word.get('interpretation', '')
                if show_meaning else word.get('vocabulary', '')
            )
            correct_label, correct_text = '1', fallback

        def print_answer(prefix: str):
            """显示正确答案；中文释义按词性分行，并保持缩进对齐。"""
            if not show_meaning:
                print(f"{prefix}{correct_label}. {correct_text}")
            elif correct_slot == 4:
                print(f"{prefix}{correct_label}. 以上三个都不对")
                print("   正确释义:")
                print(format_meaning(
                    correct_detail, first_prefix="     ", cont_prefix="     "
                ))
            else:
                print(format_option(
                    correct_label, correct_text, indent="", prefix=prefix
                ))

        hint = False
        prompt = (
            "\n请选择 (1/2/3/4/5, 9=重新播放, 0=返回): "
            if allow_replay else "\n请选择 (1/2/3/4/5/0): "
        )
        while True:
            if allow_replay:
                choice = self._prompt_choice_with_replay(prompt)
            else:
                choice = self._prompt_choice(prompt)

            if choice == '0':
                return None
            if choice == '9' and allow_replay:
                print("🔊 重新播放...")
                self.speak_sync(word['vocabulary'])
                hint = True
                continue

            correct = choice == correct_label
            if correct:
                print_answer("✅ 正确! 答案: ")
            elif choice == '5':
                print_answer("❌ 选择「不确定」视为答错! 正确答案是: ")
            else:
                print_answer("❌ 错误! 正确答案是: ")
            return {
                'correct': correct,
                'correct_label': correct_label,
                'correct_text': correct_text,
                'hint': hint,
            }
    
    def _prompt_choice(self, prompt: str = "\n请选择 (1/2/3/4/5/0): ") -> str:
        """读取并校验选择题输入，接受 1-5 以及 0（返回主菜单）"""
        valid = {'1', '2', '3', '4', '5', '0'}
        while True:
            user_choice = input(prompt).strip()
            if user_choice in valid:
                return user_choice
            print("❌ 输入无效，请输入 1/2/3/4/5 中的一个数字，或输入 0 返回主菜单")

    def _prompt_choice_with_replay(self, prompt: str = "\n请选择 (1/2/3/4/5, 9=重新播放, 0=返回): ") -> str:
        """读取并校验选择题输入，接受 1-5、9（重新播放）以及 0（返回）"""
        valid = {'1', '2', '3', '4', '5', '9', '0'}
        while True:
            user_choice = input(prompt).strip()
            if user_choice in valid:
                return user_choice
            print("❌ 输入无效，请输入 1/2/3/4/5 中的一个数字，或输入 9 重新播放，或输入 0 返回")
    
    def _word_tag(self, word: Dict, practice_mode: bool = False) -> str:
        if practice_mode:
            return " 🎯"
        if word.get('is_new'):
            return " 🆕"
        if word.get('is_extra'):
            return " 🧠"
        return ""

    def _print_extra_hint(self, words: List[Dict]):
        extra = sum(1 for word in words if word.get('is_extra'))
        if extra:
            print(f"🧠 其中 {extra} 个为巩固词：优先今日已学、记忆不牢的单词")

    def mode_english_to_chinese(self):
        """模式1: 看英文，选择中文（选择题模式）"""
        print("\n" + "="*60)
        print("\033[1;32m📖 模式1: 看英文，选择中文\033[0m")
        print("="*60)
        
        practice_mode = False
        study_words = self.get_today_study_words(self.study_count)
        if not study_words:
            study_words, practice_mode = self._fallback_practice("学习")
            if not study_words:
                return
        self._print_extra_hint(study_words)
        
        correct_count = 0
        total_count = len(study_words)
        
        for idx, word in enumerate(study_words, 1):
            phonetic = word['phonetic_us'] or ''
            tag = self._word_tag(word, practice_mode)
            print(f"\n📝 {idx}/{total_count}. \033[1;33m{word['vocabulary']}\033[0m   {phonetic}{tag}")
            
            self.speak(word['vocabulary'])
            
            started = time.perf_counter()
            result = self._ask_quiz(word, 'interpretation')
            if result is None:
                print("\n👋 返回主菜单")
                return
            duration_ms = int((time.perf_counter() - started) * 1000)
            
            self.record_review(
                word, result['correct'], duration_ms,
                hint=result['hint'], practice=practice_mode,
            )
            if result['correct']:
                correct_count += 1
        
        print(f"\n" + "="*60)
        print(f"✅ 学习完成! 正确率: {correct_count}/{total_count}")
        print("="*60)
    
    def mode_chinese_to_english(self):
        """模式2: 看中文，选择英文（选择题模式）"""
        print("\n" + "="*60)
        print("\033[1;32m📖 模式2: 看中文，选择英文\033[0m")
        print("="*60)
        
        practice_mode = False
        review_words = self.get_today_review_words(self.study_count)
        if not review_words:
            review_words, practice_mode = self._fallback_practice("复习")
            if not review_words:
                return
        self._print_extra_hint(review_words)
        
        correct_count = 0
        total_count = len(review_words)
        
        for idx, word in enumerate(review_words, 1):
            tag = self._word_tag(word, practice_mode)
            print(f"\n❓ {idx}/{total_count}. \033[1;33m"
                  f"{format_meaning(word['interpretation'], cont_prefix='   ')}"
                  f"\033[0m{tag}")
            
            started = time.perf_counter()
            result = self._ask_quiz(word, 'vocabulary')
            if result is None:
                print("\n👋 返回主菜单")
                return
            duration_ms = int((time.perf_counter() - started) * 1000)
            
            self.record_review(
                word, result['correct'], duration_ms,
                hint=result['hint'], practice=practice_mode,
            )
            if result['correct']:
                correct_count += 1
            
            self.speak(word['vocabulary'])
        
        print(f"\n" + "="*60)
        print(f"✅ 复习完成! 正确率: {correct_count}/{total_count}")
        print("="*60)
    
    def mode_chinese_to_spelling(self):
        """模式3: 看中文，拼写英文"""
        print("\n" + "="*60)
        print("\033[1;32m📖 模式3: 看中文，拼写英文 (拼写模式)\033[0m")
        print("="*60)
        
        practice_mode = False
        review_words = self.get_today_review_words(self.study_count)
        if not review_words:
            review_words, practice_mode = self._fallback_practice("复习")
            if not review_words:
                return
        self._print_extra_hint(review_words)
        
        correct_count = 0
        total_count = len(review_words)
        
        for idx, word in enumerate(review_words, 1):
            tag = self._word_tag(word, practice_mode)
            print(f"\n📝 {idx}/{total_count}. \033[1;33m"
                  f"{format_meaning(word['interpretation'], cont_prefix='   ')}"
                  f"\033[0m{tag}")
            print()
            
            started = time.perf_counter()
            user_input = input("请拼写英文单词: ").strip().lower()
            duration_ms = int((time.perf_counter() - started) * 1000)
            
            if user_input == '0':
                print("\n👋 返回主菜单")
                return
            
            correct = user_input == word['vocabulary'].lower()
            if correct:
                print("✅ 拼写正确!")
                correct_count += 1
            else:
                print(f"❌ 拼写错误! 正确答案是: {word['vocabulary']}")
            
            self.record_review(word, correct, duration_ms, practice=practice_mode)
            
            self.speak(word['vocabulary'])
        
        print(f"\n" + "="*60)
        print(f"✅ 拼写练习完成! 正确率: {correct_count}/{total_count}")
        print("="*60)
    
    def mode_listen_to_chinese(self):
        """模式4: 听音选中（听发音，选择中文释义）"""
        print("\n" + "="*60)
        print("\033[1;32m🎧 模式4: 听音选中\033[0m")
        print("="*60)
        print("💡 只播放单词发音，不显示单词，请根据发音选择正确的中文释义")
        print("💡 输入 9 可重新播放发音")
        print("="*60)
        
        practice_mode = False
        study_words = self.get_today_study_words(self.study_count)
        if not study_words:
            study_words, practice_mode = self._fallback_practice("学习")
            if not study_words:
                return
        self._print_extra_hint(study_words)
        
        correct_count = 0
        total_count = len(study_words)
        
        for idx, word in enumerate(study_words, 1):
            print(f"\n🔊 第 {idx}/{total_count} 题{self._word_tag(word, practice_mode)}")
            print("正在播放单词发音...")
            
            self.speak_sync(word['vocabulary'])
            
            started = time.perf_counter()
            result = self._ask_quiz(word, 'interpretation', allow_replay=True)
            if result is None:
                print("\n👋 返回主菜单")
                return
            duration_ms = int((time.perf_counter() - started) * 1000)
            
            self.record_review(
                word, result['correct'], duration_ms,
                hint=result['hint'], practice=practice_mode,
            )
            if result['correct']:
                correct_count += 1
        
        print(f"\n" + "="*60)
        print(f"✅ 学习完成! 正确率: {correct_count}/{total_count}")
        print("="*60)
    
    def add_to_wrong_words(self, voc_id: str):
        """将单词添加到错题本"""
        today = datetime.now().strftime('%Y%m%d')
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT vc_vocabulary, vc_interpretation, vc_phonetic_us FROM VOC_TB WHERE vc_id = ?', (voc_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return
        
        vocabulary, interpretation, phonetic_us = row
        
        conn = sqlite3.connect(self.study_db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT id, wrong_count FROM wrong_words WHERE voc_id = ?', (voc_id,))
        existing = cursor.fetchone()
        
        if existing:
            cursor.execute('''
                UPDATE wrong_words 
                SET wrong_count = ?, last_wrong_date = ?
                WHERE voc_id = ?
            ''', (existing[1] + 1, today, voc_id))
        else:
            cursor.execute('''
                INSERT INTO wrong_words (voc_id, vocabulary, interpretation, phonetic_us, add_date, last_wrong_date)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (voc_id, vocabulary, interpretation, phonetic_us, today, today))
        
        conn.commit()
        conn.close()
    
    def get_today_wrong_words(self) -> List[Dict]:
        """获取今日易错单词"""
        today = datetime.now().strftime('%Y%m%d')
        conn = sqlite3.connect(self.study_db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT voc_id, vocabulary, interpretation, phonetic_us, wrong_count 
            FROM wrong_words 
            WHERE last_wrong_date = ?
            ORDER BY wrong_count DESC
        ''', (today,))
        
        words = []
        for row in cursor.fetchall():
            words.append({
                'voc_id': row[0],
                'vocabulary': row[1],
                'interpretation': row[2],
                'phonetic_us': row[3],
                'wrong_count': row[4]
            })
        
        conn.close()
        return words
    
    def get_all_wrong_words(self) -> List[Dict]:
        """获取所有易错单词（按最后错误日期排序）"""
        conn = sqlite3.connect(self.study_db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT voc_id, vocabulary, interpretation, phonetic_us, wrong_count, last_wrong_date 
            FROM wrong_words 
            ORDER BY last_wrong_date DESC
        ''')
        
        words = []
        for row in cursor.fetchall():
            words.append({
                'voc_id': row[0],
                'vocabulary': row[1],
                'interpretation': row[2],
                'phonetic_us': row[3],
                'wrong_count': row[4],
                'last_wrong_date': row[5]
            })
        
        conn.close()
        return words
    
    def view_today_wrong_words(self):
        """查看今日易错单词"""
        words = self.get_today_wrong_words()
        if not words:
            print("\n❌ 今天没有易错单词")
            return
        
        print("\n" + "="*60)
        print("📝 今日易错单词")
        print("="*60)
        
        while True:
            for i, word in enumerate(words, 1):
                print(f"\n{i}. {word['vocabulary']}")
                if word['phonetic_us']:
                    print(f"   音标: {word['phonetic_us']}")
                print("   释义: "
                      f"{format_meaning(word['interpretation'], cont_prefix='         ')}")
                print(f"   错误次数: {word['wrong_count']}")
            
            print("\n" + "="*60)
            print("[播放:输入编号] [复习:r] [返回:0]")
            choice = input("\n请输入操作: ").strip()
            
            if choice == '0':
                break
            elif choice.lower() == 'r':
                self.review_wrong_words(words)
                break
            else:
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(words):
                        word = words[idx]
                        print(f"\n🔊 播放: {word['vocabulary']}")
                        self.speak(word['vocabulary'])
                    else:
                        print("❌ 无效编号")
                except ValueError:
                    print("❌ 请输入有效的编号")
    
    def view_all_wrong_words(self):
        """查看所有易错单词"""
        words = self.get_all_wrong_words()
        if not words:
            print("\n❌ 没有易错单词")
            return
        
        print("\n" + "="*60)
        print("📝 所有易错单词")
        print("="*60)
        
        while True:
            for i, word in enumerate(words, 1):
                print(f"\n{i}. {word['vocabulary']}")
                if word['phonetic_us']:
                    print(f"   音标: {word['phonetic_us']}")
                print("   释义: "
                      f"{format_meaning(word['interpretation'], cont_prefix='         ')}")
                print(f"   错误次数: {word['wrong_count']}")
                print(f"   最后错误: {word['last_wrong_date']}")
            
            print("\n" + "="*60)
            print("[播放:输入编号] [复习:r] [返回:0]")
            choice = input("\n请输入操作: ").strip()
            
            if choice == '0':
                break
            elif choice.lower() == 'r':
                self.review_wrong_words(words)
                break
            else:
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(words):
                        word = words[idx]
                        print(f"\n🔊 播放: {word['vocabulary']}")
                        self.speak(word['vocabulary'])
                    else:
                        print("❌ 无效编号")
                except ValueError:
                    print("❌ 请输入有效的编号")
    
    def review_wrong_words(self, words: List[Dict]):
        """复习错题单词"""
        if not words:
            print("❌ 没有要复习的错题")
            return
        
        while True:
            print("\n" + "="*60)
            print("📖 错题复习模式")
            print("="*60)
            print("1. 看英选中")
            print("2. 看中选英")
            print("3. 看中拼写英")
            print("4. 听音选中")
            print("0. 返回")
            print("="*60)
            
            choice = input("请选择复习模式 (0-4): ").strip()
            
            if choice == '0':
                break
            elif choice == '1':
                self.mode_english_to_chinese_with_words(words)
            elif choice == '2':
                self.mode_chinese_to_english_with_words(words)
            elif choice == '3':
                self.mode_chinese_to_spelling_with_words(words)
            elif choice == '4':
                self.mode_listen_to_chinese_with_words(words)
            else:
                print("❌ 无效选择")
    
    def mode_english_to_chinese_with_words(self, words: List[Dict], practice=None):
        """指定单词列表的看英选中模式；practice=None 时按到期状态自动判断"""
        print("\n" + "="*60)
        print("\033[1;32m📖 模式1: 看英选中\033[0m")
        print("="*60)
        
        correct_count = 0
        total_count = len(words)
        
        for idx, word in enumerate(words, 1):
            word = self._normalize_word(word)
            word_id = word['vc_id']
            do_schedule = (not practice) if practice is not None else self.is_card_due(word_id)
            
            phonetic = word['phonetic_us'] or ''
            print(f"\n📝 {idx}/{total_count}. \033[1;33m{word['vocabulary']}\033[0m   {phonetic}")
            
            self.speak(word['vocabulary'])
            
            started = time.perf_counter()
            result = self._ask_quiz(word, 'interpretation')
            if result is None:
                print("\n👋 返回")
                return
            duration_ms = int((time.perf_counter() - started) * 1000)
            
            self.record_review(
                word, result['correct'], duration_ms,
                hint=result['hint'], practice=not do_schedule,
            )
            if result['correct']:
                correct_count += 1
        
        print(f"\n" + "="*60)
        print(f"✅ 复习完成! 正确率: {correct_count}/{total_count}")
        print("="*60)
    
    def mode_chinese_to_english_with_words(self, words: List[Dict], practice=None):
        """指定单词列表的看中选英模式；practice=None 时按到期状态自动判断"""
        print("\n" + "="*60)
        print("\033[1;32m📖 模式2: 看中选英\033[0m")
        print("="*60)
        
        correct_count = 0
        total_count = len(words)
        
        for idx, word in enumerate(words, 1):
            word = self._normalize_word(word)
            word_id = word['vc_id']
            do_schedule = (not practice) if practice is not None else self.is_card_due(word_id)
            
            print(f"\n❓ {idx}/{total_count}. \033[1;33m"
                  f"{format_meaning(word['interpretation'], cont_prefix='   ')}"
                  f"\033[0m")
            
            started = time.perf_counter()
            result = self._ask_quiz(word, 'vocabulary')
            if result is None:
                print("\n👋 返回")
                return
            duration_ms = int((time.perf_counter() - started) * 1000)
            
            self.record_review(
                word, result['correct'], duration_ms,
                hint=result['hint'], practice=not do_schedule,
            )
            if result['correct']:
                correct_count += 1
            
            self.speak(word['vocabulary'])
        
        print(f"\n" + "="*60)
        print(f"✅ 复习完成! 正确率: {correct_count}/{total_count}")
        print("="*60)
    
    def mode_chinese_to_spelling_with_words(self, words: List[Dict], practice=None):
        """指定单词列表的看中拼写模式；practice=None 时按到期状态自动判断"""
        print("\n" + "="*60)
        print("\033[1;32m📖 模式3: 看中拼写英\033[0m")
        print("="*60)
        
        correct_count = 0
        total_count = len(words)
        
        for idx, word in enumerate(words, 1):
            word = self._normalize_word(word)
            word_id = word['vc_id']
            do_schedule = (not practice) if practice is not None else self.is_card_due(word_id)
            
            print(f"\n📝 {idx}/{total_count}. \033[1;33m"
                  f"{format_meaning(word['interpretation'], cont_prefix='   ')}"
                  f"\033[0m")
            print()
            
            started = time.perf_counter()
            user_input = input("请拼写英文单词: ").strip().lower()
            duration_ms = int((time.perf_counter() - started) * 1000)
            
            if user_input == '0':
                print("\n👋 返回")
                return
            
            correct = user_input == word['vocabulary'].lower()
            if correct:
                print("✅ 拼写正确!")
                correct_count += 1
            else:
                print(f"❌ 拼写错误! 正确答案是: {word['vocabulary']}")
            
            self.record_review(
                word, correct, duration_ms, practice=not do_schedule
            )
            
            self.speak(word['vocabulary'])
        
        print(f"\n" + "="*60)
        print(f"✅ 拼写练习完成! 正确率: {correct_count}/{total_count}")
        print("="*60)
    
    def mode_listen_to_chinese_with_words(self, words: List[Dict], practice=None):
        """指定单词列表的听音选中模式；practice=None 时按到期状态自动判断"""
        print("\n" + "="*60)
        print("\033[1;32m🎧 模式4: 听音选中\033[0m")
        print("="*60)
        print("💡 只播放单词发音，不显示单词，请根据发音选择正确的中文释义")
        print("💡 输入 9 可重新播放发音")
        print("="*60)
        
        correct_count = 0
        total_count = len(words)
        
        for idx, word in enumerate(words, 1):
            word = self._normalize_word(word)
            word_id = word['vc_id']
            do_schedule = (not practice) if practice is not None else self.is_card_due(word_id)
            
            print(f"\n🔊 第 {idx}/{total_count} 题")
            print("正在播放单词发音...")
            
            self.speak_sync(word['vocabulary'])
            
            started = time.perf_counter()
            result = self._ask_quiz(word, 'interpretation', allow_replay=True)
            if result is None:
                print("\n👋 返回")
                return
            duration_ms = int((time.perf_counter() - started) * 1000)
            
            self.record_review(
                word, result['correct'], duration_ms,
                hint=result['hint'], practice=not do_schedule,
            )
            if result['correct']:
                correct_count += 1
        
        print(f"\n" + "="*60)
        print(f"✅ 复习完成! 正确率: {correct_count}/{total_count}")
        print("="*60)
    
    def wrong_words_menu(self):
        """错题本主菜单"""
        while True:
            print("\n" + "="*60)
            print("📚 错题本")
            print("="*60)
            print("1. 查看今日易错单词")
            print("2. 查看所有易错单词")
            print("0. 返回")
            print("="*60)
            
            choice = input("请选择 (0-2): ").strip()
            
            if choice == '0':
                break
            elif choice == '1':
                self.view_today_wrong_words()
            elif choice == '2':
                self.view_all_wrong_words()
            else:
                print("❌ 无效选择")
    
    def _is_valid_phrase(self, text: str) -> bool:
        """检查例句是否有效（过滤乱码和Base64数据）"""
        if not text or len(text) < 5:
            return False
        
        special_chars = ['/', '+', '=', '_', '-']
        special_count = sum(1 for c in text if c in special_chars)
        
        if special_count > len(text) * 0.1:
            return False
        
        if any(c.isupper() and not c.isalpha() for c in text):
            pass
        
        alpha_count = sum(1 for c in text if c.isalpha())
        if alpha_count < len(text) * 0.5:
            return False
        
        common_words = ['the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
                       'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
                       'could', 'should', 'may', 'might', 'must', 'shall', 'can',
                       'need', 'dare', 'ought', 'used', 'to', 'of', 'in', 'for',
                       'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through',
                       'during', 'before', 'after', 'above', 'below', 'between',
                       'under', 'again', 'further', 'then', 'once', 'here', 'there',
                       'when', 'where', 'why', 'how', 'all', 'each', 'few', 'more',
                       'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only',
                       'own', 'same', 'so', 'than', 'too', 'very', 'just', 'but',
                       'if', 'or', 'because', 'while', 'although', 'though', 'that',
                       'which', 'who', 'whom', 'this', 'these', 'those', 'i', 'you',
                       'he', 'she', 'it', 'we', 'they', 'what', 'which', 'who']
        
        text_lower = text.lower()
        words = text_lower.split()
        if len(words) >= 2:
            for word in words[:4]:
                if word in common_words:
                    return True
        
        return False
    
    def get_word_phrases(self, voc_id: str) -> List[Dict]:
        """获取单词的例句（过滤无效数据）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT phrase, interpretation FROM PR_TB
            WHERE voc_id = ?
            LIMIT 5
        ''', (voc_id,))
        
        phrases = []
        for row in cursor.fetchall():
            english = row[0]
            chinese = row[1]
            
            if self._is_valid_phrase(english):
                phrases.append({
                    'english': english,
                    'chinese': chinese
                })
            
            if len(phrases) >= 3:
                break
        
        conn.close()
        return phrases
    
    def play_today_words(self):
        """播放今日复习单词及例句"""
        print("\n" + "="*60)
        print("\033[1;32m🔊 播放今日复习单词及例句\033[0m")
        print("="*60)
        print("按 Ctrl+C 退出播放")
        print("="*60)
        
        words = self.get_today_review_words(self.study_count)
        if not words:
            self._print_empty_session_hint("复习")
            return
        
        import time
        
        try:
            while True:
                for idx, word in enumerate(words, 1):
                    print(f"\n[{idx}/{len(words)}]")
                    print(f"单词: {word['vocabulary']}")
                    if word['phonetic_us']:
                        print(f"音标: {word['phonetic_us']}")
                    print("释义: "
                          f"{format_meaning(word['interpretation'], cont_prefix='      ')}")
                    
                    for i in range(3):
                        print(f"  播放 [{i+1}/3]...", end='\r')
                        self.speak_sync(word['vocabulary'])
                    
                    print("  播放完成        ")
                    
                    phrases = self.get_word_phrases(word['vc_id'])
                    if phrases:
                        print("\n例句:")
                        for j, phrase in enumerate(phrases, 1):
                            print(f"  {j}. {phrase['english']}")
                            print(f"     {phrase['chinese']}")
                            self.speak_sync(phrase['english'])
                
                print("\n--- 循环播放，按 Ctrl+C 退出 ---")
                
        except KeyboardInterrupt:
            print("\n\n👋 退出播放")
    
    def view_today_phrases(self):
        """今日例句功能"""
        while True:
            print("\n" + "="*60)
            print("📖 今日例句")
            print("="*60)
            print("1. 导入今日例句")
            print("2. 循环展示播放例句")
            print("3. 设置播放速度")
            print("0. 返回")
            print("="*60)
            
            choice = input("请选择 (0-3): ").strip()
            
            if choice == '0':
                break
            elif choice == '1':
                self.import_today_phrases()
            elif choice == '2':
                self.play_today_phrases()
            elif choice == '3':
                self.set_playback_speed()
            else:
                print("❌ 无效选择")
    
    def set_playback_speed(self):
        """设置语音播放速度（持久保存）"""
        print("\n" + "="*60)
        print("⚡ 设置播放速度")
        print("="*60)
        print(f"当前速度: {self.tts_speed}x")
        print("提示: 0.5-2.0 之间的数值，1.0为正常速度")
        print("="*60)
        
        while True:
            speed_input = input("请输入播放速度 (输入0返回): ").strip()
            
            if speed_input == '0':
                break
            
            try:
                speed = float(speed_input)
                
                if speed < 0.5 or speed > 2.0:
                    print("❌ 速度范围必须在 0.5-2.0 之间")
                    continue
                
                # 计算Edge TTS格式的语速
                # Edge TTS使用百分比，0%为正常语速
                # 1.0 = 0%, 1.2 = +20%, 0.8 = -20%
                percent = round((speed - 1.0) * 100)
                self.tts_rate = f"{percent:+d}%"
                self.tts_speed = speed
                
                # 保存到配置文件
                config = {}
                if os.path.exists(self.config_path):
                    with open(self.config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                
                config['tts_speed'] = speed
                
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
                
                print(f"✅ 播放速度已设置为 {speed}x ({self.tts_rate})")
                print("💾 设置已保存，下次启动时自动生效")
                break
                
            except ValueError:
                print("❌ 请输入有效的数字")
    
    def import_today_phrases(self):
        """导入今日例句（支持手动输入和PDF导入）"""
        print("\n" + "="*60)
        print("📥 导入例句")
        print("="*60)
        print("1. 手动输入例句")
        print("2. 从PDF文件导入")
        print("0. 返回")
        print("="*60)
        
        choice = input("请选择导入方式: ").strip()
        
        if choice == '0':
            return
        elif choice == '1':
            self._import_phrases_manual()
        elif choice == '2':
            self._import_phrases_from_pdf()
        else:
            print("❌ 无效选择")
    
    def _import_phrases_manual(self):
        """手动输入例句"""
        print("\n" + "="*60)
        print("📝 手动输入例句")
        print("="*60)
        print("输入0返回上一级")
        print("="*60)
        
        today = datetime.now().strftime('%Y%m%d')
        conn = sqlite3.connect(self.study_db_path)
        cursor = conn.cursor()
        
        try:
            while True:
                english = input("\n输入英语例句: ").strip()
                if english == '0':
                    break
                
                if not english:
                    print("❌ 请输入英语例句")
                    continue
                
                chinese = input("输入中文释义: ").strip()
                if chinese == '0':
                    break
                
                if not chinese:
                    print("❌ 请输入中文释义")
                    continue
                
                cursor.execute('''
                    INSERT INTO phrases (english, chinese, add_date)
                    VALUES (?, ?, ?)
                ''', (english, chinese, today))
                
                conn.commit()
                print("✅ 已添加例句")
                
                continue_input = input("继续添加? (y/n): ").strip().lower()
                if continue_input != 'y':
                    break
            
        except Exception as e:
            print(f"\n❌ 导入失败: {str(e)}")
        finally:
            conn.close()
    
    def _import_phrases_from_pdf(self):
        """从PDF文件导入例句"""
        print("\n" + "="*60)
        print("📄 从PDF导入例句")
        print("="*60)
        
        # 获取PDF文件路径
        status, pdf_path = self._choose_file_dialog(
            "选择要导入的例句 PDF",
            [("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )

        if status == 'ok':
            print(f"📄 已选择: {pdf_path}")
        elif status == 'cancel':
            print("❌ 未选择文件，已取消导入")
            return
        else:
            print("⚠️ 无法打开文件选择框，请手动输入路径")
            pdf_path = input("请输入PDF文件路径（或直接拖拽文件到此处）: ").strip().strip('"')

            if pdf_path == '0':
                return

            if not pdf_path:
                print("❌ 请输入文件路径")
                return
        
        # 检查文件是否存在
        import os
        if not os.path.exists(pdf_path):
            print(f"❌ 文件不存在: {pdf_path}")
            return
        
        if not pdf_path.lower().endswith('.pdf'):
            print("❌ 请选择PDF文件")
            return
        
        print("\n⏳ 正在读取PDF文件...")
        
        full_text = ""
        
        try:
            full_text = self._read_pdf_text(pdf_path)
            print("✅ 使用pypdf成功读取PDF")
        except Exception as e:
            print(f"⚠️ PDF文本提取失败: {str(e)}")
        
        # 方案3：如果以上都失败，尝试OCR（处理扫描版PDF）
        if not full_text.strip():
            print("⚠️ 文本提取失败，尝试OCR识别...")
            try:
                from PIL import Image
                import pytesseract
                import pdf2image
                
                # 需要安装poppler: https://github.com/Belval/pdf2image
                images = pdf2image.convert_from_path(pdf_path)
                all_text = []
                
                for img in images:
                    text = pytesseract.image_to_string(img, lang='eng+chi_sim')
                    if text:
                        all_text.append(text)
                
                full_text = '\n'.join(all_text)
                print(f"✅ 使用OCR成功识别 {len(images)} 页")
                
            except Exception as e3:
                print(f"❌ OCR识别失败（需要安装Tesseract和poppler）: {str(e3)}")
                return
        
        if not full_text.strip():
            print("❌ PDF文件为空或无法提取文本")
            return
        
        # 解析英语和中文配对
        phrases = self._parse_phrases_from_text(full_text)
        
        if not phrases:
            print("❌ 未找到有效的例句对")
            return
        
        print(f"\n📊 找到 {len(phrases)} 条例句")
        print("\n" + "="*60)
        print("前5条例句预览:")
        print("="*60)
        for i, (eng, chi) in enumerate(phrases[:5], 1):
            print(f"\n{i}. 英文: {eng[:50]}{'...' if len(eng) > 50 else ''}")
            print(f"   中文: {chi[:50]}{'...' if len(chi) > 50 else ''}")
        
        confirm = input("\n是否导入这些例句? (y/n): ").strip().lower()
        
        if confirm != 'y':
            print("❌ 已取消导入")
            return
        
        # 保存到数据库（先删除今日已有的例句，只保留本次导入的）
        today = datetime.now().strftime('%Y%m%d')
        conn = sqlite3.connect(self.study_db_path)
        cursor = conn.cursor()
        
        # 删除今日已有的例句
        cursor.execute('DELETE FROM phrases WHERE add_date = ?', (today,))
        
        # 插入新例句
        import_count = 0
        for english, chinese in phrases:
            try:
                cursor.execute('''
                    INSERT INTO phrases (english, chinese, add_date)
                    VALUES (?, ?, ?)
                ''', (english, chinese, today))
                import_count += 1
            except Exception:
                continue
        
        conn.commit()
        conn.close()
        
        print(f"\n✅ 成功导入 {import_count} 条例句（已覆盖今日之前的例句）")
    
    def _parse_phrases_from_text(self, text: str) -> List[tuple]:
        """解析文本中的英语和中文例句对（支持对话格式，处理跨行句子）"""
        import re
        
        phrases = []
        lines = text.split('\n')
        
        # 预处理：过滤空行和无效行
        filtered_lines = []
        for line in lines:
            line = line.strip()
            # 过滤乱码、无效行、纯数字和日期
            if self._is_valid_line(line) and not self._is_date_or_number(line):
                filtered_lines.append(line)
        
        # 对话格式：英语 -> 中文 -> 英语 -> 中文...
        i = 0
        while i < len(filtered_lines) - 1:
            # 收集完整的英语句子（可能跨行）
            english_line = filtered_lines[i]
            
            # 如果当前行是英语
            if self._is_english_sentence(english_line):
                # 如果当前行不以句号结尾，说明句子不完整，需要合并后续行
                j = i + 1
                while j < len(filtered_lines) and not english_line.endswith(('.', '?', '!')):
                    next_line = filtered_lines[j]
                    # 只合并英语行，遇到中文行则停止
                    if self._is_english_sentence(next_line):
                        english_line += ' ' + next_line
                        j += 1
                    else:
                        break
                
                # 现在查找中文翻译（跳过已合并的行）
                chinese_line = ""
                k = j
                while k < len(filtered_lines):
                    if self._is_chinese_sentence(filtered_lines[k]):
                        chinese_line = filtered_lines[k]
                        # 检查中文是否跨行
                        l = k + 1
                        while l < len(filtered_lines):
                            next_chinese = filtered_lines[l]
                            if self._is_chinese_sentence(next_chinese) and not self._is_english_sentence(next_chinese):
                                chinese_line += next_chinese
                                l += 1
                            else:
                                break
                        break
                    k += 1
                
                if english_line and chinese_line:
                    english = self._clean_sentence(english_line)
                    chinese = self._clean_sentence(chinese_line)
                    
                    if english and chinese and len(english) > 2 and len(chinese) > 1:
                        phrases.append((english, chinese))
                
                # 移动到下一对
                i = k + 1 if chinese_line else i + 1
            else:
                # 不是英语句子，跳过
                i += 1
        
        return phrases
    
    def _is_date_or_number(self, line: str) -> bool:
        """检查是否是日期或纯数字（不过滤包含数字的英语句子）"""
        import re
        
        # 检查日期格式（纯日期）
        date_patterns = [
            r'^\d{1,2}/\d{1,2}/\d{2,4}$',      # MM/DD/YYYY 或 MM/DD/YY
            r'^\d{4}-\d{1,2}-\d{1,2}$',          # YYYY-MM-DD
            r'^\d{1,2}(st|nd|rd|th)?$',          # 纯数字加后缀如 25th
        ]
        
        for pattern in date_patterns:
            if re.match(pattern, line):
                return True
        
        # 检查纯数字或卡号格式（不包含字母）
        if re.match(r'^(\d+\s*)+$', line):
            # 只过滤纯数字，不过滤包含字母的句子
            if not any(c.isalpha() for c in line):
                return True
        
        return False
    
    def _clean_sentence(self, line: str) -> str:
        """清理句子，移除首尾的空格和特殊字符（保留数字）"""
        import re
        
        # 只移除首尾的空格和特殊字符，保留数字
        line = re.sub(r'^[\s\-\.,]+', '', line)
        line = re.sub(r'[\s\-\.,]+$', '', line)
        
        return line.strip()
    
    def _is_valid_line(self, line: str) -> bool:
        """检查行是否有效（过滤乱码和无效内容）"""
        import re
        
        if not line or len(line) < 2:
            return False
        
        # 过滤 Test/Section/Unit 等章节标题行
        if re.match(r'^(test|section|part|unit|module)\s*\d+', line, re.I):
            return False
        
        # 中英文标点均视为正常字符，避免“那好，谢谢。再见。”这类句子被误删
        allowed_punct = '.,?!:;()"\'，。、！？：；“”‘’（）—…-'
        special_chars = sum(
            1 for c in line
            if not c.isalnum() and not c.isspace()
            and c not in allowed_punct
            and not ('\u4e00' <= c <= '\u9fff')
            and not ('\u3000' <= c <= '\u303f')
            and not ('\uff00' <= c <= '\uffef')
        )
        
        # 对于短句子（长度<=5），放宽特殊字符限制
        if len(line) <= 5:
            if special_chars > len(line) * 0.5:
                return False
        else:
            if special_chars > len(line) * 0.3:
                return False
        
        # 至少包含字母、数字或汉字
        return any(c.isalpha() or c.isdigit() or '\u4e00' <= c <= '\u9fff' for c in line)
    
    def _extract_english_from_line(self, line: str) -> str:
        """从包含中英文的句子中提取英文部分"""
        # 找到最后一个中文字符的位置
        last_chinese_pos = 0
        for j, char in enumerate(line):
            if '\u4e00' <= char <= '\u9fff':
                last_chinese_pos = j
        
        if last_chinese_pos > 0:
            return line[:last_chinese_pos].strip()
        return line.strip()
    
    def _extract_chinese_from_line(self, line: str) -> str:
        """从包含中英文的句子中提取中文部分"""
        chinese_chars = []
        for char in line:
            if '\u4e00' <= char <= '\u9fff':
                chinese_chars.append(char)
        
        if chinese_chars:
            return ''.join(chinese_chars)
        return ""
    
    def _is_english_sentence(self, text: str) -> bool:
        """检查是否是英语句子（不包含中文）"""
        if not text or len(text) < 3:
            return False
        
        # 如果包含中文字符，则不是纯英语句子
        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in text)
        if has_chinese:
            return False
        
        # 计算英文字符占比
        english_chars = sum(1 for c in text if c.isascii() and c.isalpha())
        total_chars = sum(1 for c in text if c.isalpha())
        
        if total_chars == 0:
            return False
        
        english_ratio = english_chars / total_chars
        return english_ratio > 0.5
    
    def _is_chinese_sentence(self, text: str) -> bool:
        """检查是否是中文句子"""
        if not text or len(text) < 2:
            return False
        
        # 计算中文字符占比
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        
        if chinese_chars == 0:
            # 纯数字+全角标点的译文行（如“752239。”）没有汉字，也按译文处理
            has_fullwidth = any('\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef' for c in text)
            has_ascii_letter = any(c.isascii() and c.isalpha() for c in text)
            return has_fullwidth and not has_ascii_letter
        
        # 中文开头的行视为译文，兼容“是的，是P-R-E-S-C-O，double T.”这类中英混排
        if '\u4e00' <= text[0] <= '\u9fff':
            return True
        
        return chinese_chars > len(text) * 0.5
    
    def _contains_both_languages(self, text: str) -> bool:
        """检查是否是中英混合的标题行（用于过滤）"""
        if not text:
            return False
        
        # 统计英文字母数量
        english_count = sum(1 for c in text if c.isalpha())
        
        # 统计中文字符数量
        chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        
        # 只有当英文字母占比>=30%且中文字符占比>=30%时，才认为是混合标题
        # 这样可以过滤掉"Going to the gym 去体育馆"这种标题，但保留正常的中文句子
        total_chars = len(text)
        if total_chars == 0:
            return False
        
        english_ratio = english_count / total_chars
        chinese_ratio = chinese_count / total_chars
        
        return english_ratio >= 0.3 and chinese_ratio >= 0.3
    
    def play_today_phrases(self):
        """循环播放今日导入的例句（按照导入顺序）"""
        today = datetime.now().strftime('%Y%m%d')
        
        conn = sqlite3.connect(self.study_db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT english, chinese FROM phrases WHERE add_date = ? ORDER BY id ASC', (today,))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            print("\n❌ 今天还没有添加任何例句")
            return
        
        print("\n" + "="*60)
        print(f"📖 循环播放今日例句（共 {len(rows)} 条，按 Ctrl+C 退出）")
        print("="*60)
        
        try:
            while True:
                for i, (english, chinese) in enumerate(rows, 1):
                    print(f"\n[{i}/{len(rows)}]")
                    print(f"例句: {english}")
                    print(f"释义: {chinese}")
                    
                    self.speak_sync(english)
                
        except KeyboardInterrupt:
            print("\n\n👋 退出播放")
    
    def view_today_added_words(self):
        """查看今天添加的单词（支持删除）"""
        while True:
            conn = sqlite3.connect(self.study_db_path)
            cursor = conn.cursor()
            
            today = datetime.now().strftime('%Y%m%d')
            cursor.execute('''
                SELECT voc_id, vocabulary FROM study_list
                WHERE add_date = ?
                ORDER BY add_date DESC
            ''', (today,))
            
            words_data = cursor.fetchall()
            conn.close()
            
            if not words_data:
                print("\n❌ 今天还没有添加任何单词")
                return
            
            words = self.enrich_words(words_data)
            
            print("\n" + "="*60)
            print("📝 今日添加的单词")
            print("="*60)
            print(f"今日共添加了 {len(words)} 个单词:\n")
            
            for i, word in enumerate(words, 1):
                print(f"{i}. {word['vocabulary']}")
                if word['phonetic_us']:
                    print(f"   美标: {word['phonetic_us']}")
                print("   释义: "
                      f"{format_meaning(word['interpretation'], cont_prefix='         ')}")
                print()
            
            print("="*60)
            print("[删除:输入编号] [导出:e] [返回:0]")
            choice = input("\n请输入操作: ").strip()
            
            if choice == '0':
                break
            
            if choice.lower() == 'e':
                self.export_today_words(words)
                continue
            
            try:
                index = int(choice) - 1
                if 0 <= index < len(words):
                    word_to_remove = words[index]
                    confirm = input(f"确定要删除 '{word_to_remove['vocabulary']}' 吗? (y/n): ").strip().lower()
                    if confirm == 'y':
                        self.remove_from_study_list(word_to_remove['vc_id'])
                        print(f"✅ 已删除 '{word_to_remove['vocabulary']}'")
                else:
                    print("❌ 无效编号")
            except ValueError:
                print("❌ 请输入有效的编号")
    
    def export_today_words(self, words: List[Dict]):
        """导出今日添加的单词到txt文件"""
        today = datetime.now().strftime('%Y%m%d')
        filename = f"{today}.txt"
        
        content = f"今日添加的单词 ({len(words)}个)\n"
        content += "="*50 + "\n\n"
        
        for i, word in enumerate(words, 1):
            content += f"{i}. {word['vocabulary']}\n"
            if word['phonetic_us']:
                content += f"   美标: {word['phonetic_us']}\n"
            content += ("   释义: "
                        + format_meaning(word['interpretation'], cont_prefix="         ")
                        + "\n")
            content += "\n"
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"\n✅ 成功导出到文件: {filename}")
        except Exception as e:
            print(f"\n❌ 导出失败: {str(e)}")
    
    def remove_from_study_list(self, voc_id: str) -> bool:
        """从学习列表中删除单词（同时清掉它的复习日志）"""
        conn = sqlite3.connect(self.study_db_path)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM study_list WHERE voc_id = ?', (voc_id,))
        deleted = cursor.rowcount > 0
        cursor.execute('DELETE FROM review_log WHERE voc_id = ?', (voc_id,))
        
        conn.commit()
        conn.close()
        return deleted
    
    def search_add_menu(self):
        """搜索并添加单词子菜单"""
        while True:
            print("\n" + "="*60)
            print("🔍 搜索并添加单词")
            print("="*60)
            print("7.1 🔎 直接搜索单词")
            print("7.2 📄 从PDF导入单词")
            print("0. 返回")
            print("="*60)

            choice = input("请选择 (7.1/7.2/0): ").strip()

            if choice == '0':
                break
            elif choice in ('7.1', '1'):
                self.interactive_search()
            elif choice in ('7.2', '2'):
                self.import_words_from_pdf()
            else:
                print("❌ 无效选择")

    def _read_pdf_text(self, pdf_path: str) -> str:
        """提取PDF文本（优先pypdf，兼容旧版PyPDF2）"""
        try:
            from pypdf import PdfReader
        except ImportError:
            try:
                from PyPDF2 import PdfReader
            except ImportError:
                raise RuntimeError("缺少PDF解析库，请运行: pip install pypdf")

        reader = PdfReader(pdf_path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)

    def _extract_entries_from_pdf_text(self, text: str) -> List[Dict]:
        """从PDF文本中提取 (英文单词, 中文释义) 对

        同一单词只保留首次拼写；若首次出现没带释义，用后面出现的中文补齐。
        """
        import re

        header_words = {
            'no', 'word', 'meaning', 'date', 'page', 'name',
        }
        header_labels = {
            '单词', '释义', '中文', '中文释义', '英文', '词义', '音标',
            '序号', '编号', '页码', '例句', '词组',
        }
        pos_words = {
            'n', 'v', 'vt', 'vi', 'adj', 'adv', 'prep', 'pron',
            'conj', 'pl', 'etc', 'noun', 'verb', 'adjective', 'adverb',
        }
        word_pattern = r"[A-Za-z](?:[A-Za-z'’]|-(?=[A-Za-z]))*"
        cjk_pattern = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff]')

        entries = []
        index = {}
        last_entry = None

        def is_header_label(text: str) -> bool:
            stripped = re.sub(r'[\s\d.、:：]+', '', text or '')
            if not stripped:
                return False
            for label in sorted(header_labels, key=len, reverse=True):
                stripped = stripped.replace(label, '')
            return not stripped

        def add(token: str, meaning: str = ''):
            nonlocal last_entry
            token = token.strip().strip("-'’")
            if len(token) < 2 or not re.fullmatch(word_pattern, token):
                return
            key = token.lower()
            if key in pos_words:
                return
            if key in header_words and (not meaning or is_header_label(meaning)):
                return
            entry = index.get(key)
            if entry is None:
                entry = {'word': token, 'meaning': ''}
                index[key] = entry
                entries.append(entry)
            if not entry['meaning'] and meaning:
                entry['meaning'] = meaning
            last_entry = entry

        for raw in text.splitlines():
            line = raw.replace('\u00a0', ' ').replace('\u3000', ' ').strip()
            if not line:
                continue
            # 去掉音标，避免把 [ə'bændən] 里的字母当成单词
            line = re.sub(r'\[[^\]]*\]', ' ', line)
            line = re.sub(r'/[^/]{0,40}/', ' ', line)

            tokens = []
            for match in re.finditer(word_pattern, line):
                token = match.group(0).strip("-'’")
                if len(token) < 2:
                    continue
                if token.lower() in pos_words:
                    continue
                tokens.append((match.start(), match.end(), token))

            if not tokens:
                # 双行词表：英文一行、中文释义下一行
                if is_header_label(line):
                    continue
                if (cjk_pattern.search(line) and last_entry is not None
                        and not last_entry['meaning']):
                    last_entry['meaning'] = re.sub(
                        r'(?:\s+\d+)+\s*$', '', line
                    ).strip()
                continue

            # 每个英文词后面的文字就是它的释义（兼容单栏和双栏词表）
            for i, (_, end, token) in enumerate(tokens):
                next_start = tokens[i + 1][0] if i + 1 < len(tokens) else len(line)
                segment = line[end:next_start]
                # 去掉释义末尾的页码/编号，如 "放弃 26"
                segment = re.sub(r'(?:\s+\d+)+\s*$', '', segment).strip()
                meaning = segment if cjk_pattern.search(segment) else ''
                add(token, meaning)

        return entries

    def _lookup_words_exact(self, words: List[str]):
        """在词库中精确匹配单词，返回 (匹配列表, 未找到列表)"""
        matched = []
        missing = []
        conn = sqlite3.connect(self.db_path)
        try:
            for word in words:
                rows = conn.execute(
                    '''
                    SELECT vc_id, vc_vocabulary, vc_interpretation, vc_phonetic_us
                    FROM VOC_TB
                    WHERE LOWER(vc_vocabulary) = ?
                    ''',
                    (word.lower(),),
                ).fetchall()
                if not rows:
                    missing.append(word)
                    continue
                # 同一单词可能在多本词书中出现：优先大小写一致、有音标、释义更精简的条目
                rows.sort(key=lambda r: (
                    r[1] != word,
                    0 if r[3] else 1,
                    len(r[2] or ''),
                ))
                row = rows[0]
                matched.append({
                    'vc_id': row[0],
                    'vocabulary': row[1],
                    'interpretation': row[2],
                    'phonetic_us': row[3],
                })
        finally:
            conn.close()
        return matched, missing

    def _insert_missing_words(self, entries: List[Dict]) -> List[Dict]:
        """把词库中不存在的单词直接新增到本地词库 VOC_TB

        entries: [{'word': 'abandon', 'meaning': '放弃'}, ...]
        返回新增后的词条，结构与 _lookup_words_exact 的返回一致。
        """
        prepared = []
        seen = set()
        for entry in entries or []:
            word = (entry.get('word') or '').strip()
            if not word:
                continue
            key = word.lower()
            if key in seen:
                continue
            seen.add(key)
            vc_id = hashlib.md5(f"import:{key}".encode('utf-8')).hexdigest()[:24]
            prepared.append({
                'key': key,
                'info': {
                    'vc_id': vc_id,
                    'vocabulary': word,
                    'interpretation': (entry.get('meaning') or '').strip(),
                    'phonetic_us': '',
                },
            })
        if not prepared:
            return []

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(self.db_path)
        try:
            # 先确认词库里确实没有这些单词，避免重复新增同名单词
            existing = set()
            keys = [item['key'] for item in prepared]
            for start in range(0, len(keys), 500):
                chunk = keys[start:start + 500]
                placeholders = ','.join('?' * len(chunk))
                rows = conn.execute(
                    f'SELECT LOWER(vc_vocabulary) FROM VOC_TB '
                    f'WHERE LOWER(vc_vocabulary) IN ({placeholders})',
                    chunk,
                ).fetchall()
                existing.update(row[0] for row in rows)

            rows_to_insert = []
            for item in prepared:
                if item['key'] in existing:
                    continue
                info = item['info']
                word = info['vocabulary']
                rows_to_insert.append((
                    info['vc_id'],
                    None,
                    word[:100],
                    word[0].upper() if word else '',
                    '',
                    info['phonetic_us'],
                    info['interpretation'],
                    '',
                    0,
                    0.0,
                    '',
                    now,
                    0,
                    0.0,
                ))

            if rows_to_insert:
                conn.executemany(
                    """INSERT OR IGNORE INTO VOC_TB
                       (vc_id, original_id, vc_vocabulary, vc_initial,
                        vc_phonetic_uk, vc_phonetic_us, vc_interpretation,
                        vc_pronunciation, vc_frequency, vc_difficulty,
                        vc_phrase, vc_updated_time, vc_study_user_count,
                        vc_acknowledge_rate)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    rows_to_insert,
                )
                conn.commit()

            inserted = []
            ids = [row[0] for row in rows_to_insert]
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                placeholders = ','.join('?' * len(chunk))
                rows = conn.execute(
                    f'''SELECT vc_id, vc_vocabulary, vc_interpretation,
                               vc_phonetic_us
                        FROM VOC_TB WHERE vc_id IN ({placeholders})''',
                    chunk,
                ).fetchall()
                for row in rows:
                    inserted.append({
                        'vc_id': row[0],
                        'vocabulary': row[1],
                        'interpretation': row[2],
                        'phonetic_us': row[3],
                    })
        finally:
            conn.close()
        return inserted

    @staticmethod
    def _choose_file_dialog(title: str, filetypes):
        """弹出系统文件选择框。返回 (状态, 路径)，状态为 ok/cancel/unavailable"""
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception:
            return 'unavailable', ''

        root = None
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            root.update()
            path = filedialog.askopenfilename(
                parent=root, title=title, filetypes=filetypes
            )
        except Exception:
            return 'unavailable', ''
        finally:
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass

        return ('ok', path) if path else ('cancel', '')

    def _overwrite_local_entries(self, words: List[Dict]) -> int:
        """用导入的英文拼写和中文释义覆盖本地词库，并同步学习列表/错题本的快照

        返回被覆盖的单词数量。
        """
        vocab_rows = []
        study_rows = []
        wrong_rows = []
        for word in words:
            imported_word = word.get('imported_word')
            imported_meaning = word.get('imported_meaning')
            if not imported_word and not imported_meaning:
                continue
            vocab = imported_word or word['vocabulary']
            meaning = imported_meaning or (word.get('interpretation') or '')
            if vocab == word['vocabulary'] and meaning == (word.get('interpretation') or ''):
                continue
            vocab_rows.append((vocab, meaning, word['vc_id']))
            study_rows.append((vocab, word['vc_id']))
            wrong_rows.append((vocab, meaning, word['vc_id']))

        if not vocab_rows:
            return 0

        conn = sqlite3.connect(self.db_path)
        try:
            conn.executemany(
                'UPDATE VOC_TB SET vc_vocabulary = ?, vc_interpretation = ? WHERE vc_id = ?',
                vocab_rows,
            )
            conn.commit()
        finally:
            conn.close()

        conn = sqlite3.connect(self.study_db_path)
        try:
            conn.executemany(
                'UPDATE study_list SET vocabulary = ? WHERE voc_id = ?', study_rows
            )
            conn.executemany(
                'UPDATE wrong_words SET vocabulary = ?, interpretation = ? WHERE voc_id = ?',
                wrong_rows,
            )
            conn.commit()
        finally:
            conn.close()

        return len(vocab_rows)

    def import_words_from_pdf(self):
        """从PDF词表导入单词到学习列表"""
        print("\n" + "="*60)
        print("📄 从PDF导入单词")
        print("="*60)

        status, pdf_path = self._choose_file_dialog(
            "选择要导入的单词表 PDF",
            [("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )

        if status == 'ok':
            print(f"📄 已选择: {pdf_path}")
        elif status == 'cancel':
            print("❌ 未选择文件，已取消导入")
            return
        else:
            print("⚠️ 无法打开文件选择框，请手动输入路径")
            default_path = "dc.pdf"
            hint = f"回车使用 {default_path}" if os.path.exists(default_path) else "输入0返回"
            pdf_path = input(f"PDF文件路径 ({hint}): ").strip().strip('"')

            if pdf_path == '0':
                return
            if not pdf_path:
                if not os.path.exists(default_path):
                    print("❌ 请输入文件路径")
                    return
                pdf_path = default_path

        if not os.path.exists(pdf_path):
            print(f"❌ 文件不存在: {pdf_path}")
            return
        if not pdf_path.lower().endswith('.pdf'):
            print("❌ 请选择PDF文件")
            return

        print("\n⏳ 正在读取PDF文件...")
        try:
            text = self._read_pdf_text(pdf_path)
        except Exception as e:
            print(f"❌ 无法读取PDF文件: {e}")
            return

        entries = self._extract_entries_from_pdf_text(text)
        if not entries:
            print("❌ 未从PDF中识别到英文单词")
            return

        print(f"📄 识别到 {len(entries)} 个单词，正在词库中匹配...")
        matched, missing = self._lookup_words_exact([e['word'] for e in entries])

        entry_by_key = {e['word'].lower(): e for e in entries}
        for word in matched:
            entry = entry_by_key.get(word['vocabulary'].lower())
            if entry:
                word['imported_word'] = entry['word']
                word['imported_meaning'] = entry['meaning']

        # 词库里没有的单词直接新增入库，不再丢弃
        new_words = []
        if missing:
            missing_entries = [
                entry_by_key[word.lower()] for word in missing
                if word.lower() in entry_by_key
            ]
            try:
                new_words = self._insert_missing_words(missing_entries)
            except Exception as e:
                print(f"⚠️ 新增词库失败: {e}")

        added_keys = {w['vocabulary'].lower() for w in new_words}
        still_missing = [w for w in missing if w.lower() not in added_keys]

        # 按PDF中的顺序合并，保持导入时的词表顺序
        word_by_key = {w['vocabulary'].lower(): w for w in matched}
        for word in new_words:
            word_by_key.setdefault(word['vocabulary'].lower(), word)
        words = []
        seen_keys = set()
        for entry in entries:
            key = entry['word'].lower()
            if key in seen_keys:
                continue
            word = word_by_key.get(key)
            if word is not None:
                seen_keys.add(key)
                words.append(word)

        print("\n" + "="*60)
        print(f"词库已有 {len(matched)} 个 | 新增入库 {len(new_words)} 个 | "
              f"可加入学习列表 {len(words)} 个")
        print("="*60)
        for i, word in enumerate(words, 1):
            display_word = word.get('imported_word') or word['vocabulary']
            meaning = (word.get('imported_meaning') or word['interpretation'] or '')
            print(f"{i:2d}. {display_word}")
            print(format_meaning(meaning, first_prefix="    ", cont_prefix="    "))

        overwrite_count = sum(
            1 for w in words
            if (w.get('imported_word') and w['imported_word'] != w['vocabulary'])
            or (w.get('imported_meaning')
                and w['imported_meaning'] != (w.get('interpretation') or ''))
        )
        blank_meaning = [
            w['vocabulary'] for w in new_words
            if not (w.get('interpretation') or '').strip()
        ]

        if still_missing:
            print(f"\n⚠️ 有 {len(still_missing)} 个单词新增失败，未能加入学习列表:")
            print("   " + ", ".join(still_missing))
        if blank_meaning:
            print(f"\nℹ️ {len(blank_meaning)} 个新增单词没有中文释义，学习时会显示为空:")
            print("   " + ", ".join(blank_meaning))

        if not words:
            print("\n❌ 没有可添加的单词")
            return

        prompt = f"\n将 {len(words)} 个单词加入学习列表"
        if new_words:
            prompt += f"，并在本地词库新增 {len(new_words)} 个单词"
        if overwrite_count:
            prompt += f"，并用导入的英文和释义覆盖本地词库 {overwrite_count} 条"
        confirm = input(prompt + "? (y/n): ").strip().lower()
        if confirm != 'y':
            if new_words:
                print(f"ℹ️ 已新增到本地词库的 {len(new_words)} 个单词会保留，"
                      "之后可在 7.1 中搜索添加")
            print("❌ 已取消导入")
            return

        updated = self._overwrite_local_entries(words)
        for word in words:
            if word.get('imported_word'):
                word['vocabulary'] = word['imported_word']
            if word.get('imported_meaning'):
                word['interpretation'] = word['imported_meaning']
        added = self.add_to_study_list(words)
        skipped = len(words) - added
        if new_words:
            print(f"✨ 已在本地词库新增 {len(new_words)} 个单词")
        if updated:
            print(f"✏️ 已用导入内容覆盖本地词库 {updated} 条")
        print(f"\n✅ 成功添加 {added} 个单词到学习列表")
        if skipped:
            print(f"ℹ️ {skipped} 个单词已在学习列表中，未重复添加")
        if added:
            print("💡 回到主菜单选择 1-4 开始学习")

    def interactive_search(self):
        """交互式搜索并添加单词"""
        print("\n" + "="*60)
        print("🔍 搜索并添加单词")
        print("="*60)
        
        keyword = input("输入要搜索的英文单词 (精准搜索，输入0返回): ").strip()
        if not keyword or keyword == '0':
            return
        
        words = self.search_words(keyword, limit=20)
        if not words:
            print(f"❌ 词库中未找到 '{keyword}'")
            if not re.fullmatch(r"[A-Za-z][A-Za-z'’\-]{0,49}", keyword):
                return
            confirm = input("是否直接新增该单词到本地词库? (y/n): ").strip().lower()
            if confirm != 'y':
                return
            meaning = input("请输入中文释义 (可留空): ").strip()
            try:
                new_words = self._insert_missing_words(
                    [{'word': keyword, 'meaning': meaning}]
                )
            except Exception as e:
                print(f"❌ 新增单词失败: {e}")
                return
            if not new_words:
                print("❌ 该单词已在词库中")
                return
            added = self.add_to_study_list(new_words)
            print(f"✨ 已在本地词库新增单词，并添加 {added} 个到学习列表")
            return
        
        print(f"\n✅ 找到 {len(words)} 个单词:")
        for i, word in enumerate(words, 1):
            print(f"{i}. {word['vocabulary']}")
            print(format_meaning(
                word['interpretation'], first_prefix="   ", cont_prefix="   "
            ))
        
        selected = input("\n请选择要添加的单词 (输入编号，用逗号分隔，如: 1,2,3): ").strip()
        
        try:
            indices = [int(x.strip()) - 1 for x in selected.split(',')]
            selected_words = [words[i] for i in indices if 0 <= i < len(words)]
            
            added = self.add_to_study_list(selected_words)
            print(f"\n✅ 成功添加 {added} 个单词到学习列表")
        except (ValueError, IndexError):
            print("❌ 输入无效")
    
    def main_menu(self):
        """主菜单"""
        while True:
            stats = self.get_session_stats()
            print("\n" + "="*60)
            print("🎓 英语单词学习工具")
            print("="*60)
            print(f"📊 学习列表 {stats['total']} 词 | 今日到期 {stats['due']} | "
                  f"新词 {stats['new_introduced']}/{stats['new_limit']} | "
                  f"待学新词 {stats['new_available']}")
            if stats['extra_available']:
                print(f"🧠 今日巩固 {stats['extra_available']} 词（今日已学及薄弱词）")
            print("="*60)
            print("1. 📖 看英选中 (带发音)")
            print("2. 📝 看中选英")
            print("3. 🔤 看中拼写英")
            print("4. 🎧 听音选中 (听发音选中文)")
            print("5. 🔊 语音播放")
            print("6. 📓 错题本")
            print("7. 🔍 搜索并添加单词")
            print("8. 📖 雅思阅读训练")
            print("9. 🔧 其他设置")
            print("10. 🎯 自由练习 (不影响复习计划)")
            print("11. 🌱 自由学习 (认识/不认识，直到全部学会)")
            print("0. 退出")
            print("="*60)
            
            choice = input("请选择 (0-11): ").strip()
            
            if choice == '0':
                print("\n👋 再见!")
                break
            elif choice == '1':
                self.mode_english_to_chinese()
            elif choice == '2':
                self.mode_chinese_to_english()
            elif choice == '3':
                self.mode_chinese_to_spelling()
            elif choice == '4':
                self.mode_listen_to_chinese()
            elif choice == '5':
                self.audio_playback_menu()
            elif choice == '6':
                self.wrong_words_menu()
            elif choice == '7':
                self.search_add_menu()
            elif choice == '8':
                self.reading_practice()
            elif choice == '9':
                self.other_settings_menu()
            elif choice == '10':
                self.free_practice_menu()
            elif choice == '11':
                self.free_learning()
            else:
                print("❌ 无效选择")

    def free_practice_menu(self):
        """自由练习：不写复习计划，只记录练习日志"""
        print("\n" + "="*60)
        print("🎯 自由练习")
        print("="*60)
        
        words = self.get_practice_words(self.study_count)
        if not words:
            print("❌ 学习列表为空，请先搜索并添加单词")
            return
        
        print(f"本次随机抽取 {len(words)} 个单词，练习结果不影响复习计划")
        print("1. 看英选中")
        print("2. 看中选英")
        print("3. 看中拼写英")
        print("4. 听音选中")
        print("0. 返回")
        print("="*60)
        
        choice = input("请选择练习模式 (0-4): ").strip()
        if choice == '0':
            return
        elif choice == '1':
            self.mode_english_to_chinese_with_words(words, practice=True)
        elif choice == '2':
            self.mode_chinese_to_english_with_words(words, practice=True)
        elif choice == '3':
            self.mode_chinese_to_spelling_with_words(words, practice=True)
        elif choice == '4':
            self.mode_listen_to_chinese_with_words(words, practice=True)
        else:
            print("❌ 无效选择")
    
    def free_learning(self):
        """自由学习：认识/不认识二选一，不认识的词看释义和例句后重新排队，直到全部认识"""
        print("\n" + "="*60)
        print("\033[1;32m🌱 自由学习\033[0m")
        print("="*60)

        words = self.get_session_words(self.study_count)
        source = "今日学习队列"
        if not words:
            words = self.get_practice_words(self.study_count)
            source = "历史单词（今天没有待学的词）"
        if not words:
            print("❌ 学习列表为空，请先用「7. 搜索并添加单词」添加单词")
            return

        queue = list(words)
        learned = 0
        print(f"本次从{source}取 {len(queue)} 个单词，全部认识后结束")
        print("1 = 认识   2 = 不认识   3 = 🔊 重播发音   0 = 返回")
        print("="*60)

        while queue:
            word = queue.pop(0)
            print("\n" + "-"*60)
            print(f"📖 队列剩余 {len(queue) + 1} 个 | 已学会 {learned} 个")
            print(f"\n   {word.get('vocabulary', '')}")
            phonetic = word.get('phonetic_us') or word.get('phonetic_uk')
            if phonetic:
                print(f"   [美] {phonetic}")
            print("   🔊 自动播放（按 3 可重播）")
            print()
            self.speak_sync(word.get('vocabulary', ''))

            while True:
                choice = self._prompt_recognition()
                if choice == '0':
                    print(f"\n👋 本次已学会 {learned} 个单词")
                    return
                if choice == '1':
                    self.record_review(word, correct=True, duration_ms=0)
                    learned += 1
                    print("✅ 已学会")
                    break
                if choice == '3':
                    self.speak_sync(word.get('vocabulary', ''))
                    continue

                examples = self._show_word_answer(word)
                if not self._prompt_answer_playback(word, examples):
                    print(f"\n👋 本次已学会 {learned} 个单词")
                    return
                queue.append(word)
                print(f"🔁 稍后会再考你一次（队列还有 {len(queue)} 个）")
                break

        print("\n" + "="*60)
        print(f"🎉 全部学会！本次共学会 {learned} 个单词")
        print("="*60)

    def _prompt_recognition(self) -> str:
        while True:
            choice = input("请选择 (1=认识, 2=不认识, 3=🔊播放发音, 0=返回): ").strip()
            if choice in ('0', '1', '2', '3'):
                return choice
            print("❌ 输入无效，请输入 1、2、3 或 0")

    def _show_word_answer(self, word):
        """不认识时展示中文释义和例句，返回例句列表供播放"""
        print("\n📝 中文释义:")
        interpretation = (word.get('interpretation') or '').strip() or '暂无释义'
        for line in meaning_lines(interpretation):
            print(f"   {line}")

        examples = self.get_word_phrases(word.get('vc_id') or word.get('voc_id'))
        if not examples:
            voc_id = str(word.get('vc_id') or word.get('voc_id') or '')
            conn = sqlite3.connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT vc_phrase FROM VOC_TB WHERE vc_id = ?", (voc_id,)
                ).fetchone()
            finally:
                conn.close()
            if row and row[0]:
                examples = [{'english': row[0], 'chinese': ''}]

        if examples:
            print("\n💬 例句:")
            for example in examples[:2]:
                print(f"   {example['english']}")
                if example.get('chinese'):
                    print(f"   {example['chinese']}")
            print("\n   🔊 按 4 播放例句")
        else:
            print("\n💬 例句: 暂无例句")
        return examples

    def _prompt_answer_playback(self, word, examples) -> bool:
        """展示答案后等待操作；返回 False 表示用户选择退出"""
        has_example = bool(examples)
        tips = "0=返回   3=🔊播放单词"
        if has_example:
            tips += "   4=🔊播放例句"
        print(f"\n{tips}   回车=下一个")
        while True:
            choice = input(">>> ").strip()
            if choice == '':
                return True
            if choice == '0':
                return False
            if choice == '3':
                self.speak_sync(word.get('vocabulary', ''))
            elif choice == '4' and has_example:
                self.speak_sync(examples[0]['english'])
            else:
                print(f"❌ 输入无效，{tips}，或直接回车继续")
    
    def reading_practice(self):
        """雅思阅读训练"""
        try:
            from reading_practice import ReadingPractice
        except ImportError:
            print("❌ 未找到 reading_practice.py")
            return
        centered = sys.stdout if isinstance(sys.stdout, CenteredStdout) else None
        if centered is not None:
            centered.set_enabled(False)
        try:
            ReadingPractice(self.study_db_path).run()
        finally:
            if centered is not None:
                centered.set_enabled(True)
    
    def audio_playback_menu(self):
        """语音播放子菜单"""
        while True:
            print("\n" + "="*60)
            print("🔊 语音播放")
            print("="*60)
            print("5.1 📖 今日例句")
            print("5.2 🔊 播放今日复习单词及例句")
            print("5.3 🎧 听英语故事")
            print("0. 返回")
            print("="*60)
            
            choice = input("请选择 (0/5.1/5.2/5.3): ").strip()
            
            if choice == '0':
                break
            elif choice == '5.1' or choice == '1':
                self.view_today_phrases()
            elif choice == '5.2' or choice == '2':
                self.play_today_words()
            elif choice == '5.3' or choice == '3':
                self.play_english_story()
            else:
                print("❌ 无效选择")
    
    def play_english_story(self):
        """播放英语故事"""
        stories = [
            {
                "title": "The Little Prince - Chapter 1",
                "text": "Once when I was six years old I saw a magnificent picture in a book, called True Stories from Nature, about the primeval forest. It showed a boa constrictor in the act of swallowing an animal. Here is a copy of the drawing. In the book it said: 'Boa constrictors swallow their prey whole, without chewing it. After that they are not able to move, and they sleep through the six months that they need for digestion.'",
                "chinese": "当我六岁的时候，我在一本叫做《自然的真实故事》的书上，看到了一幅精彩的图画，画的是原始森林里的情景。画里一条蟒蛇正在吞吃一只野兽。这幅画的摹本就在下面。这本书上写道：'蟒蛇把它们的猎物不加咀嚼地整个吞下去。之后它们就无法动弹了，它们会在长达六个月的睡眠中消化这些食物。'"
            },
            {
                "title": "The Three Little Pigs",
                "text": "Once upon a time there were three little pigs. They left their mother and went out to build their own houses. The first pig built his house of straw. The second pig built his house of sticks. The third pig built his house of bricks. One day a big bad wolf came. He blew down the straw house and the stick house. But he could not blow down the brick house. The three little pigs lived happily ever after.",
                "chinese": "从前有三只小猪。它们离开妈妈，出去建造自己的房子。第一只小猪用稻草建房子。第二只小猪用树枝建房子。第三只小猪用砖块建房子。有一天来了一只大灰狼。他吹倒了稻草房和树枝房。但他吹不倒砖房。三只小猪从此幸福地生活在一起。"
            },
            {
                "title": "The Fox and the Grapes",
                "text": "One hot summer day, a fox was walking through an orchard. He stopped before a bunch of grapes. They were ripe and juicy. 'I'm just feeling thirsty,' he thought. So he backed up a few paces, got a running start, jumped, but could not reach the grapes. He walked back. One, two, three, he jumped up again, but still, he missed the grapes. The fox tried again and again, but never succeeded. At last he decided to give it up. He walked away with his nose in the air, saying, 'I am sure they are sour.'",
                "chinese": "一个炎热的夏日，一只狐狸走过果园。他在一串葡萄前停了下来。葡萄熟透了，汁水饱满。'我正觉得口渴呢，'他想。于是他退后几步，助跑，跳跃，但够不到葡萄。他走回去。一、二、三，他又跳起来，但还是没够到葡萄。狐狸试了一次又一次，但始终没有成功。最后他决定放弃。他昂着头走开了，说：'我敢肯定这些葡萄是酸的。'"
            },
            {
                "title": "The Tortoise and the Hare",
                "text": "Once upon a time there was a hare who was very proud of his speed. One day he challenged a tortoise to a race. The tortoise accepted. The race started, and the hare ran very fast. Soon he was far ahead. He thought he had plenty of time to rest, so he lay down and fell asleep. The tortoise kept walking slowly but steadily. When the hare woke up, he saw the tortoise was almost at the finish line. He ran as fast as he could, but it was too late. The tortoise won the race.",
                "chinese": "从前有一只兔子，他对自己的速度非常自豪。有一天，他向一只乌龟挑战赛跑。乌龟接受了挑战。比赛开始了，兔子跑得非常快。很快他就遥遥领先了。他觉得有足够的时间休息，于是躺下来睡着了。乌龟继续缓慢而稳定地走着。当兔子醒来时，他看到乌龟几乎到达终点线了。他拼命地跑，但为时已晚。乌龟赢得了比赛。"
            },
            {
                "title": "The Ant and the Grasshopper",
                "text": "In a field one summer's day, a Grasshopper was hopping about, chirping and singing to its heart's content. An Ant passed by, bearing along with great toil an ear of corn he was taking to the nest. 'Why not come and chat with me,' said the Grasshopper, 'instead of toiling and moiling in that way?' 'I am helping to lay up food for the winter,' said the Ant, 'and recommend you to do the same.' 'Why bother about winter?' said the Grasshopper; we have got plenty of food at present.' But the Ant went on its way and continued its toil. When the winter came the Grasshopper had no food and found itself dying of hunger. While it saw the ants distributing every day corn and grain from the stores they had collected in the summer.",
                "chinese": "夏日的一天，在一片田野里，一只蚱蜢跳来跳去，尽情地唧唧叫着唱歌。一只蚂蚁经过，正吃力地拖着一穗玉米往巢穴走去。'为什么不来和我聊聊天呢，'蚱蜢说，'而要那样辛苦劳作呢？' '我在帮着储备过冬的食物，'蚂蚁说，'我建议你也这么做。' '为什么要为冬天操心呢？'蚱蜢说：'我们现在有充足的食物。'但蚂蚁继续赶路，继续劳作。冬天来了，蚱蜢没有食物，饿得奄奄一息。而它看到蚂蚁们每天都在分发夏天收集的玉米和谷物。"
            },
            {
                "title": "Beauty and the Beast",
                "text": "Once upon a time, there was a wealthy merchant who had three daughters. The youngest was called Beauty, and she was the kindest and most beautiful. One day, the merchant got lost in a storm and took shelter in a mysterious castle. The castle belonged to a terrifying Beast. The Beast agreed to let the merchant go, but only if Beauty came to live with him. Beauty agreed. At first, Beauty was afraid of the Beast, but she soon discovered he had a kind heart. They became friends. Eventually, Beauty's love broke the spell and the Beast turned back into a handsome prince.",
                "chinese": "从前有一位富商，他有三个女儿。最小的女儿名叫贝儿，她最善良也最美丽。有一天，商人在暴风雨中迷了路，在一座神秘的城堡里避雨。这座城堡属于一只可怕的野兽。野兽同意让商人离开，但条件是贝儿来和他一起生活。贝儿同意了。起初，贝儿很害怕野兽，但她很快发现他有一颗善良的心。他们成了朋友。最终，贝儿的爱打破了咒语，野兽变回了英俊的王子。"
            },
            {
                "title": "Little Red Riding Hood",
                "text": "Once upon a time, there was a little girl who wore a red riding hood. One day, her mother asked her to take some food to her grandmother who lived in the woods. On the way, she met a wolf. The wolf asked where she was going. Little Red Riding Hood told him. The wolf ran ahead to the grandmother's house, ate the grandmother, and dressed in her clothes. When Little Red Riding Hood arrived, the wolf tried to eat her too. But a woodcutter heard her cries and saved her. The wolf was killed, and everyone lived happily ever after.",
                "chinese": "从前有一个小女孩，她戴着一顶红色的小红帽。有一天，她妈妈让她带些食物给住在森林里的祖母。路上，她遇到了一只狼。狼问她要去哪里。小红帽告诉了他。狼跑到祖母家前面，吃掉了祖母，然后穿上她的衣服。当小红帽到达时，狼也想吃掉她。但一位樵夫听到了她的叫声，救了她。狼被杀死了，从此大家都过上了幸福的生活。"
            },
            {
                "title": "The Ugly Duckling",
                "text": "It was lovely summer weather in the country. A mother duck sat on her eggs. One by one, the eggs hatched. But the last egg was larger than the others. When it hatched, the duckling was big and ugly. The other ducks laughed at him and made fun of him. The ugly duckling ran away. He wandered through the countryside, facing many hardships. One winter, he saw a group of beautiful swans. He wished he could be like them. When spring came, he looked at his reflection in the water and realized he had grown into a beautiful swan. The ugly duckling was actually a swan all along.",
                "chinese": "乡下的夏天天气很宜人。一只母鸭坐在她的蛋上。蛋一个接一个地孵化了。但最后一个蛋比其他的都大。当它孵化出来时，小鸭子又大又丑。其他鸭子都嘲笑他，取笑他。丑小鸭逃跑了。他在乡间游荡，经历了许多艰难困苦。一个冬天，他看到一群美丽的天鹅。他希望自己能像他们一样。春天来临时，他看着水中的倒影，意识到自己已经长成了一只美丽的天鹅。丑小鸭其实一直都是一只天鹅。"
            }
        ]
        
        while True:
            print("\n" + "="*60)
            print("🎧 英语故事听读")
            print("="*60)
            for i, story in enumerate(stories, 1):
                print(f"{i}. {story['title']}")
            print("0. 返回")
            print("="*60)
            
            choice = input("请选择要听的故事 (0-8): ").strip()
            
            if choice == '0':
                break
            
            try:
                index = int(choice) - 1
                if 0 <= index < len(stories):
                    story = stories[index]
                    print(f"\n📖 {story['title']}")
                    print("-" * 60)
                    print(f"英文: {story['text']}")
                    print("\n中文:")
                    print(story['chinese'])
                    print("\n🎧 开始播放...")
                    
                    # 分段播放英语故事
                    sentences = story['text'].split('. ')
                    for sentence in sentences:
                        sentence = sentence.strip()
                        if sentence:
                            print(f"  {sentence}")
                            self.speak_sync(sentence)
                    
                    print("\n✅ 故事播放完成!")
                else:
                    print("❌ 无效选择")
            except ValueError:
                print("❌ 请输入有效的数字")
    
    def other_settings_menu(self):
        """其他设置子菜单"""
        while True:
            print("\n" + "="*60)
            print("🔧 其他设置")
            print("="*60)
            print("9.1 📝 查看今日添加的单词")
            print("9.2 🔧 设置每次学习数量")
            print(f"9.3 🆕 设置每日新词上限 (当前 {self.new_per_day})")
            print(f"9.4 🎯 设置目标记忆保持率 (当前 {self.desired_retention:.2f})")
            print("0. 返回")
            print("="*60)
            
            choice = input("请选择 (0/9.1/9.2/9.3/9.4): ").strip()
            
            if choice == '0':
                break
            elif choice == '9.1' or choice == '1':
                self.view_today_added_words()
            elif choice == '9.2' or choice == '2':
                self.set_study_count()
            elif choice == '9.3' or choice == '3':
                self.set_new_per_day()
            elif choice == '9.4' or choice == '4':
                self.set_desired_retention()
            else:
                print("❌ 无效选择")
    
    def set_study_count(self):
        """设置每次学习单词数量"""
        print("\n" + "="*60)
        print("🔧 设置每次学习数量")
        print("="*60)
        print(f"当前设置: 每次学习 {self.study_count} 个单词")
        print("\n请输入新的数量 (1-50，输入0返回):")
        
        try:
            new_count_input = input(">>> ").strip()
            if new_count_input == '0':
                print("\n👋 返回主菜单")
                return
            new_count = int(new_count_input)
            if 1 <= new_count <= 50:
                self.study_count = new_count
                self.save_study_count()
                print(f"✅ 已设置每次学习 {self.study_count} 个单词")
            else:
                print("❌ 请输入1-50之间的数字")
        except ValueError:
            print("❌ 请输入有效的数字")

    def set_new_per_day(self):
        """设置每日新词上限"""
        print("\n" + "="*60)
        print("🆕 设置每日新词上限")
        print("="*60)
        print(f"当前设置: 每天最多学习 {self.new_per_day} 个新词")
        print("提示: 0 表示不安排新词，只复习到期单词；建议 10-20")
        print("="*60)
        
        try:
            value = input("请输入新的上限 (0-100，输入q返回): ").strip()
            if value.lower() == 'q':
                return
            new_limit = int(value)
            if 0 <= new_limit <= 100:
                self.new_per_day = new_limit
                self._save_config({'new_per_day': new_limit})
                print(f"✅ 每日新词上限已设置为 {self.new_per_day}")
            else:
                print("❌ 请输入0-100之间的数字")
        except ValueError:
            print("❌ 请输入有效的数字")

    def set_desired_retention(self):
        """设置 FSRS 目标记忆保持率"""
        print("\n" + "="*60)
        print("🎯 设置目标记忆保持率")
        print("="*60)
        print(f"当前设置: {self.desired_retention:.2f}")
        print("提示: 0.90 表示希望记住 90% 的单词；越高复习越频繁")
        print("      常用范围 0.85-0.95，输入百分比 (如 90)")
        print("="*60)
        
        try:
            value = input("请输入保持率 (70-97，输入q返回): ").strip()
            if value.lower() == 'q':
                return
            percent = int(value)
            if 70 <= percent <= 97:
                self.desired_retention = percent / 100
                self._save_config({'desired_retention': self.desired_retention})
                self._build_engine()
                print(f"✅ 目标记忆保持率已设置为 {self.desired_retention:.2f}")
                print("💾 新设置从下一次作答开始生效")
            else:
                print("❌ 请输入70-97之间的数字")
        except ValueError:
            print("❌ 请输入有效的数字")

def main():
    """主函数"""
    try:
        install_centered_output()
        app = WordStudyApp()
        app.main_menu()
    except KeyboardInterrupt:
        print("\n\n程序已退出")
        sys.exit(0)
    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

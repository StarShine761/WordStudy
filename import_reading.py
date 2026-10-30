#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导入雅思阅读题库（hwttop5/ielts-reading-past-papers）到 word_study.db。"""

import concurrent.futures
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "ielts_reading_src")
EXAM_DIR = os.path.join(DATA_DIR, "exams")
MANIFEST = os.path.join(DATA_DIR, "manifest.json")
DB_PATH = os.path.join(BASE_DIR, "word_study.db")
CDN = "https://cdn.jsdelivr.net/gh/hwttop5/ielts-reading-past-papers@main/"
RAW = "https://raw.githubusercontent.com/hwttop5/ielts-reading-past-papers/main/"
SOURCE_URL = "https://github.com/hwttop5/ielts-reading-past-papers"

CHOICE_KINDS = {"single_choice", "multi_choice", "yes_no_not_given", "true_false_not_given"}
MATCH_KINDS = {"matching", "classification"}
BLOCK_TAGS = {
    "div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr",
    "table", "thead", "tbody", "ul", "ol", "section", "article", "blockquote",
}


# ---------------------------------------------------------------- 下载

def load_manifest():
    with open(MANIFEST, "r", encoding="utf-8") as f:
        return json.load(f)["paths"]


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def download_one(path):
    dst = os.path.join(EXAM_DIR, os.path.basename(path))
    if os.path.exists(dst) and os.path.getsize(dst) > 1000:
        return "skip"
    for i in range(3):
        try:
            data = fetch(CDN + path)
            if not data.lstrip().startswith(b"{"):
                raise ValueError("bad payload")
            with open(dst, "wb") as f:
                f.write(data)
            return "ok"
        except Exception:
            time.sleep(1 + i)
    try:
        data = fetch(RAW + path)
        with open(dst, "wb") as f:
            f.write(data)
        return "ok"
    except Exception:
        return "fail " + path


def download_all():
    os.makedirs(EXAM_DIR, exist_ok=True)
    paths = load_manifest()
    with concurrent.futures.ThreadPoolExecutor(16) as ex:
        results = list(ex.map(download_one, paths))
    failed = [r for r in results if r.startswith("fail")]
    print(f"题库文件: {len(paths)}，新下载 {results.count('ok')}，已存在 {results.count('skip')}，失败 {len(failed)}")
    for r in failed:
        print("  " + r)
    return not failed


# ---------------------------------------------------------------- 渲染

def clean_text(s):
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def render_nodes(nodes, marker=None):
    """把题库节点树渲染成纯文本；marker(node) 返回占位符或 None。"""
    out = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        ntype = node.get("type")
        if ntype == "text":
            out.append(node.get("text", ""))
            return
        if ntype == "optionChip":
            out.append("\n" + str(node.get("label", "")) + "\n")
            return
        if ntype in ("dropzone", "textInput", "select", "choiceInput"):
            placeholder = marker(node) if marker else ""
            out.append(placeholder or "")
            return
        if ntype != "element":
            return
        tag = (node.get("tag") or "").lower()
        if tag == "img":
            alt = node.get("attrs", {}).get("alt") or "图片"
            out.append(f"\n[图片：{alt}]\n")
            return
        if tag == "br":
            out.append("\n")
            return
        if tag in ("td", "th"):
            out.append(" | ")
        if tag in BLOCK_TAGS:
            out.append("\n")
        for child in node.get("children", []) or []:
            walk(child)
        if tag in BLOCK_TAGS:
            out.append("\n")

    walk(nodes)
    return clean_text("".join(out))


def plain_text(nodes):
    return render_nodes(nodes)


def walk_tree(node):
    if isinstance(node, list):
        for item in node:
            yield from walk_tree(item)
        return
    if not isinstance(node, dict):
        return
    yield node
    for key in ("children", "nodes"):
        for child in node.get(key, []) or []:
            yield from walk_tree(child)


def find_containers(nodes, class_name):
    result = []
    for node in walk_tree(nodes):
        cls = node.get("attrs", {}).get("class", "")
        if node.get("tag") == "div" and class_name in str(cls).split():
            result.append(node)
    return result


def node_qids(node):
    found = set()
    for n in walk_tree(node):
        if n.get("type") == "choiceInput":
            qid = n.get("questionId")
            if qid:
                found.add(qid)
            for extra in n.get("questionIds") or []:
                found.add(extra)
    return found


def find_choice_items(nodes):
    """找最外层“只归属一个题号”的选项容器，返回 [(qid, container)]。"""
    result = []

    def walk(node, parent_single):
        if isinstance(node, list):
            for item in node:
                walk(item, parent_single)
            return
        if not isinstance(node, dict) or node.get("type") != "element":
            return
        cls = str(node.get("attrs", {}).get("class", ""))
        if "group" in cls.split():
            for child in node.get("children") or []:
                walk(child, False)
            return
        qids = node_qids(node)
        if len(qids) == 1 and not parent_single:
            result.append((next(iter(qids)), node))
            return
        for child in node.get("children") or []:
            walk(child, len(qids) == 1)

    for n in nodes:
        walk(n, False)
    return result


def stem_from_container(container):
    """渲染容器题干，跳过选项区域（label / 含单选项的列表 / *options 容器）。"""
    out = []

    def walk(n):
        if isinstance(n, list):
            for item in n:
                walk(item)
            return
        if not isinstance(n, dict):
            return
        if n.get("type") == "choiceInput":
            return
        if n.get("type") == "text":
            out.append(n.get("text", ""))
            return
        if n.get("type") != "element":
            return
        tag = (n.get("tag") or "").lower()
        cls = set(str(n.get("attrs", {}).get("class", "")).split())
        if tag == "label" or any(c.endswith("options") or c == "options" for c in cls):
            return
        if tag in ("ul", "ol") and any(x.get("type") == "choiceInput" for x in walk_tree(n)):
            return
        if tag in BLOCK_TAGS:
            out.append("\n")
        for child in n.get("children") or []:
            walk(child)
        if tag in BLOCK_TAGS:
            out.append("\n")

    for child in container.get("children") or []:
        walk(child)
    return strip_leading_number(clean_text("".join(out)))


def container_without(node, skip_classes):
    """渲染容器，跳过指定 class 的子树。"""
    out = []

    def walk(n):
        if isinstance(n, list):
            for item in n:
                walk(item)
            return
        if not isinstance(n, dict):
            return
        cls = str(n.get("attrs", {}).get("class", ""))
        if any(c in cls.split() for c in skip_classes):
            return
        if n.get("type") == "choiceInput":
            return
        if n.get("type") == "text":
            out.append(n.get("text", ""))
            return
        if n.get("type") == "element":
            tag = (n.get("tag") or "").lower()
            if tag in BLOCK_TAGS:
                out.append("\n")
            for child in n.get("children", []) or []:
                walk(child)
            if tag in BLOCK_TAGS:
                out.append("\n")
            return
        for child in n.get("children", []) or []:
            walk(child)

    walk(node)
    return clean_text("".join(out))


# ---------------------------------------------------------------- 抽取

def strip_leading_number(text):
    return re.sub(r"^\s*\d{1,2}\s*[.)、]?\s*", "", text).strip()


def marker_for(node, display_map):
    if node.get("type") == "choiceInput":
        return ""
    qid = node.get("questionId") or ""
    if not qid:
        return ""
    number = display_map.get(qid, qid.lstrip("q"))
    return f"____({number})"


def extract_options(group):
    options = []
    seen = set()

    def add(value, label):
        value = str(value).strip()
        label = clean_text(str(label))
        if not value:
            return
        if value in seen:
            for opt in options:
                if opt["value"] == value and len(opt["label"]) < len(label):
                    opt["label"] = label
            return
        seen.add(value)
        options.append({"value": value, "label": label})

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        ntype = node.get("type")
        if ntype == "optionChip":
            add(node.get("value", ""), node.get("label", ""))
        elif ntype == "select":
            for opt in node.get("options", []) or []:
                if opt.get("value"):
                    add(opt["value"], opt.get("label", opt["value"]))
        elif ntype == "choiceInput":
            add(node.get("value", ""), node.get("value", ""))
        elif ntype == "element" and (node.get("tag") or "").lower() in ("li", "th"):
            text = plain_text([node])
            for part in text.split("\n"):
                part = part.strip()
                match = re.match(r"^([A-Z]|i{1,3}|iv|v|vi{0,3}|ix|x)\b[.)]?\s*(.*)$", part, re.S)
                if match:
                    add(match.group(1), part)
        for key in ("children", "nodes"):
            for child in node.get(key, []) or []:
                walk(child)

    walk(group.get("contentNodes", []))
    return options


def option_labels_from_container(container):
    """从 question-item 里的 radio/checkbox 选项抽取 value 和展示文本。"""
    options = []
    for node in walk_tree(container):
        if node.get("type") != "choiceInput" or not node.get("value"):
            continue
        options.append(str(node["value"]))
    return options


def label_texts(container):
    """把每个 label 渲染成 'A 选项内容' 形式，返回 [(value, text)]。"""
    result = []
    for node in walk_tree(container):
        if node.get("tag") != "label":
            continue
        text = clean_text(render_nodes([node])).replace("\n", " ")
        text = re.sub(r"\s+", " ", text)
        value = ""
        for child in walk_tree(node):
            if child.get("type") == "choiceInput" and child.get("value"):
                value = str(child["value"])
                break
        match = re.match(r"^\s*([A-Z])\b[.)]?\s*(.*)$", text)
        if match:
            result.append((match.group(1), f"{match.group(1)}. {match.group(2).strip()}"))
        elif value:
            result.append((value, text.strip()))
    return result


def instruction_text(group):
    out = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        cls = str(node.get("attrs", {}).get("class", ""))
        if "question-item" in cls or "match-question-item" in cls:
            return
        if node.get("type") == "text":
            out.append(node.get("text", ""))
            return
        if node.get("type") in ("dropzone", "textInput", "choiceInput", "select"):
            return
        if node.get("type") == "element":
            tag = (node.get("tag") or "").lower()
            if tag in BLOCK_TAGS:
                out.append("\n")
            for child in node.get("children", []) or []:
                walk(child)
            if tag in BLOCK_TAGS:
                out.append("\n")
            return
        for child in node.get("children", []) or []:
            walk(child)

    walk(group.get("contentNodes", []))
    return clean_text("".join(out))


def layout_text(group, display_map):
    text = render_nodes(group.get("contentNodes", []), lambda n: marker_for(n, display_map))
    return text


def prompt_from_marker(layout, qid, display_map):
    number = display_map.get(qid, qid.lstrip("q"))
    marker = f"____({number})"
    lines = [ln.strip() for ln in layout.split("\n")]
    for idx, line in enumerate(lines):
        if marker not in line:
            continue
        candidate = line

        def bare_len(text):
            return len(clean_text(text.replace(marker, "")).strip(" .:-•–|"))

        if bare_len(candidate) < 20:
            prevs = [ln for ln in lines[:idx] if ln]
            if prevs:
                candidate = f"{prevs[-1]} {candidate}"
        if bare_len(candidate) < 20:
            for nxt in lines[idx + 1: idx + 4]:
                if nxt:
                    candidate = f"{candidate} {nxt}"
                    break
        if len(candidate) > 260:
            sentences = re.split(r"(?<=[.!?])\s+", candidate)
            hit = next((s for s in sentences if marker in s), candidate)
            pos = sentences.index(hit)
            context = sentences[max(0, pos - 1): pos + 2]
            candidate = " ".join(context)
        candidate = re.sub(r"____\(\d+\)", "____", candidate)
        return strip_leading_number(clean_text(candidate))
    return ""


def heading_prompts(doc):
    """匹配题的小标题版：空位在正文段落里，返回 {qid: 题干}。"""
    result = {}
    for block in doc.get("passageBlocks", []):
        for node in walk_tree(block.get("nodes", [])):
            cls = str(node.get("attrs", {}).get("class", ""))
            if node.get("tag") != "div" or "paragraph-wrapper" not in cls.split():
                continue
            dropzones = [n for n in walk_tree(node) if n.get("type") == "dropzone"]
            if not dropzones:
                continue
            dz = dropzones[0]
            qid = dz.get("questionId")
            paragraph = dz.get("paragraph") or ""
            body = container_without(node.get("children", []), set())
            body = re.sub(r"^\s*[A-Z]\s+", "", body)
            first = re.split(r"(?<=[.!?])\s+", body.replace("\n", " "), maxsplit=1)[0]
            label = dz.get("labelText") or f"Paragraph {paragraph}"
            label = re.sub(r"\s*\(Q\d+\)", "", label)
            label = label.rstrip("：:").strip()
            snippet = first if len(first) <= 120 else first[:118].rstrip() + "…"
            result[qid] = clean_text(f"{label} {snippet}")
    return result


def table_prompts_by_number(layout, display_map):
    result = {}
    for line in layout.split("\n"):
        line = clean_text(line)
        if not line:
            continue
        match = re.match(r"^[|\s]*(\d{1,2})\s*[.)]?\s+(.*)$", line)
        if match and "____(" not in line:
            text = re.sub(r"^[|\s]+", "", line)
            text = re.sub(r"\s*\|\s*$", "", text)
            result[match.group(1)] = text.strip()
    return result


def content_question_ids(group):
    ids = set()

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") in ("dropzone", "textInput", "select") and node.get("questionId"):
            ids.add(node["questionId"])
        if node.get("type") == "choiceInput":
            if node.get("questionId"):
                ids.add(node["questionId"])
            for qid in node.get("questionIds") or []:
                ids.add(qid)
        for key in ("children", "nodes"):
            for child in node.get(key, []) or []:
                walk(child)

    walk(group.get("contentNodes", []))
    return ids


def extract_questions(doc):
    display_map = doc.get("questionDisplayMap", {})
    groups = []

    for seq, group in enumerate(doc.get("questionGroups", []), start=1):
        kind = group.get("kind", "")
        declared = list(group.get("questionIds", []))
        in_content = content_question_ids(group)
        if in_content and kind != "multi_choice":
            qids = [qid for qid in declared if qid in in_content]
        else:
            qids = declared
        headings = None
        if kind in MATCH_KINDS and not in_content:
            headings = heading_prompts(doc)
            if headings:
                qids = [qid for qid in qids if qid in headings]
        if not qids:
            continue
        layout = layout_text(group, display_map)
        instruction = instruction_text(group)
        pool = extract_options(group)
        questions = {}

        if kind in CHOICE_KINDS:
            for qid, container in find_choice_items(group.get("contentNodes", [])):
                if qid not in qids and qid not in display_map:
                    continue
                stem = stem_from_container(container)
                opts = label_texts(container)
                if not opts:
                    seen = set()
                    for node in walk_tree(container):
                        if node.get("type") == "choiceInput" and node.get("value"):
                            value = str(node["value"]).strip()
                            if value and value not in seen:
                                seen.add(value)
                                opts.append((value, value))
                item = questions.setdefault(qid, {"prompt": "", "options": []})
                if stem and not item["prompt"]:
                    item["prompt"] = stem
                if opts and not item["options"]:
                    item["options"] = [{"value": v, "label": t} for v, t in opts]
            if kind == "multi_choice":
                stem = ""
                for node in walk_tree(group.get("contentNodes", [])):
                    cls = str(node.get("attrs", {}).get("class", "")).split()
                    if node.get("tag") == "div" and "group" in cls:
                        rendered = stem_from_container(node)
                        lines = [ln for ln in rendered.split("\n") if ln.strip()]
                        stem = lines[-1] if lines else ""
                        break
                opts = [{"value": v, "label": t} for v, t in label_texts(group.get("contentNodes", []))]
                if not opts:
                    opts = pool
                if not stem:
                    stem = instruction.split("\n")[-1] if instruction else ""
                for qid in qids:
                    questions[qid] = {"prompt": stem, "options": opts}

        elif kind in MATCH_KINDS:
            for container in find_containers(group.get("contentNodes", []), "match-question-item"):
                qid = ""
                for node in walk_tree(container):
                    if node.get("type") in ("dropzone", "select") and node.get("questionId"):
                        qid = node["questionId"]
                        break
                if not qid:
                    continue
                stem = strip_leading_number(
                    container_without(container, {"match-dropzone", "dropzone"}))
                questions[qid] = {"prompt": stem, "options": pool}

        elif kind == "table_completion":
            by_number = table_prompts_by_number(layout, display_map)
            for qid in qids:
                number = display_map.get(qid, qid.lstrip("q"))
                questions[qid] = {"prompt": by_number.get(number, ""), "options": pool}

        # 通用兜底：按题号在排版文本里定位
        headings = None
        for qid in qids:
            item = questions.get(qid) or {}
            if not item.get("prompt"):
                if headings is None:
                    headings = heading_prompts(doc)
                prompt = headings.get(qid) or prompt_from_marker(layout, qid, display_map)
                questions[qid] = {"prompt": prompt, "options": item.get("options") or pool}

        groups.append({
            "seq": seq,
            "kind": kind,
            "instruction": instruction,
            "layout": layout,
            "options": pool,
            "qids": qids,
            "questions": questions,
        })
    return groups


def answer_variants(value):
    if isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raw = re.split(r"\s*/\s*|\s+OR\s+|;", str(value))
    return [str(x).strip() for x in raw if str(x).strip()]


def passage_text(doc):
    parts = []
    for block in doc.get("passageBlocks", []):
        text = render_nodes(block.get("nodes", []), lambda n: "")
        if text:
            parts.append(text)
    return clean_text("\n\n".join(parts))


# ---------------------------------------------------------------- 入库

SCHEMA = """
CREATE TABLE IF NOT EXISTS reading_passage (
    id TEXT PRIMARY KEY,
    title TEXT,
    category TEXT,
    frequency TEXT,
    total_questions INTEGER,
    body_text TEXT,
    source_url TEXT,
    imported_at TEXT
);
CREATE TABLE IF NOT EXISTS reading_group (
    id TEXT PRIMARY KEY,
    passage_id TEXT,
    seq INTEGER,
    kind TEXT,
    instruction TEXT,
    layout TEXT,
    options_json TEXT,
    qids_json TEXT
);
CREATE TABLE IF NOT EXISTS reading_question (
    id TEXT PRIMARY KEY,
    passage_id TEXT,
    group_id TEXT,
    qid TEXT,
    display_number TEXT,
    kind TEXT,
    prompt TEXT,
    options_json TEXT,
    answers_json TEXT
);
CREATE TABLE IF NOT EXISTS reading_attempt (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    passage_id TEXT,
    started_at TEXT,
    finished_at TEXT,
    total INTEGER,
    correct INTEGER,
    score REAL
);
CREATE TABLE IF NOT EXISTS reading_answer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER,
    question_id TEXT,
    user_answer TEXT,
    is_correct INTEGER,
    answered_at TEXT
);
CREATE TABLE IF NOT EXISTS reading_wrong (
    question_id TEXT PRIMARY KEY,
    passage_id TEXT,
    wrong_count INTEGER DEFAULT 1,
    last_user_answer TEXT,
    last_wrong_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reading_q_passage ON reading_question(passage_id);
CREATE INDEX IF NOT EXISTS idx_reading_group_passage ON reading_group(passage_id);
"""


def import_all(conn):
    cursor = conn.cursor()
    cursor.executescript(SCHEMA)
    files = sorted(f for f in os.listdir(EXAM_DIR) if f.endswith(".json"))
    now = datetime.now().isoformat(timespec="seconds")
    total_q = 0
    empty_prompts = []
    skipped = []

    for filename in files:
        with open(os.path.join(EXAM_DIR, filename), "r", encoding="utf-8") as f:
            doc = json.load(f)
        pid = doc.get("examId") or os.path.splitext(filename)[0]
        meta = doc.get("meta", {})
        cursor.execute("DELETE FROM reading_group WHERE passage_id = ?", (pid,))
        cursor.execute("DELETE FROM reading_question WHERE passage_id = ?", (pid,))
        if not any(g.get("contentNodes") for g in doc.get("questionGroups", [])):
            cursor.execute("DELETE FROM reading_passage WHERE id = ?", (pid,))
            skipped.append((pid, meta.get("title") or pid))
            continue
        cursor.execute(
            "INSERT OR REPLACE INTO reading_passage "
            "(id, title, category, frequency, total_questions, body_text, source_url, imported_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, re.sub(r"\s*【\s*】\s*", " ", meta.get("title") or pid).strip(),
             meta.get("category", ""), meta.get("frequency", ""),
             doc.get("totalQuestions", 0), passage_text(doc), SOURCE_URL, now))

        groups = extract_questions(doc)
        answers = doc.get("answerKey", {})
        display_map = doc.get("questionDisplayMap", {})
        for group in groups:
            gid = f"{pid}:g{group['seq']}"
            cursor.execute(
                "INSERT OR REPLACE INTO reading_group "
                "(id, passage_id, seq, kind, instruction, layout, options_json, qids_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (gid, pid, group["seq"], group["kind"], group["instruction"], group["layout"],
                 json.dumps(group["options"], ensure_ascii=False),
                 json.dumps(group["qids"], ensure_ascii=False)))
            for qid in group["qids"]:
                item = group["questions"].get(qid, {})
                prompt = item.get("prompt") or ""
                if not prompt:
                    empty_prompts.append(f"{pid}/{qid}")
                total_q += 1
                cursor.execute(
                    "INSERT OR REPLACE INTO reading_question "
                    "(id, passage_id, group_id, qid, display_number, kind, prompt, options_json, answers_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"{pid}:{qid}", pid, gid, qid, display_map.get(qid, qid.lstrip("q")),
                     group["kind"], prompt,
                     json.dumps(item.get("options") or [], ensure_ascii=False),
                     json.dumps(answer_variants(answers.get(qid, "")), ensure_ascii=False)))

    conn.commit()
    print(f"已导入 {len(files) - len(skipped)} 篇、{total_q} 题到 {DB_PATH}")
    if skipped:
        print(f"跳过 {len(skipped)} 篇（源文件中题目内容为空）：")
        for pid, title in skipped:
            print(f"  {pid}  {title}")
    if empty_prompts:
        print(f"警告：{len(empty_prompts)} 题未抽取到题干，前 10 个：{empty_prompts[:10]}")
    return len(files) - len(skipped), total_q


def main():
    args = set(sys.argv[1:])
    if "--skip-download" not in args:
        if not download_all():
            print("部分文件下载失败，可重试；已下载的文件会保留。")
    if not os.path.isdir(EXAM_DIR) or not os.listdir(EXAM_DIR):
        print("没有找到题库文件，请先运行下载。")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    try:
        import_all(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

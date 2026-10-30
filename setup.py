from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="word-study-tool",
    version="1.0.0",
    author="StarShine761",
    author_email="18391539+StarShine761@users.noreply.github.com",
    description="Windows 终端英语单词学习工具：FSRS 间隔重复 + Edge TTS 发音 + 词书管理 + 雅思阅读训练",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/StarShine761/WordStudy",
    packages=find_packages(include=["srs", "srs.*", "wordstudy"]),
    py_modules=[
        "word_study",
        "meaning_format",
        "reading_viewer",
        "reading_exam",
        "reading_practice",
        "import_reading",
        "rebuild_vocab_db",
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: Microsoft :: Windows",
        "Environment :: Console",
        "Natural Language :: Chinese (Simplified)",
        "Topic :: Education",
    ],
    python_requires=">=3.10",
    install_requires=[
        "edge-tts>=7.3.1",
        "fsrs==6.3.2",
        "pypdf>=6.0.0",
    ],
    entry_points={
        "console_scripts": [
            "word-study=word_study:main",
        ],
    },
)

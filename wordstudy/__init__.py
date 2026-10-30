"""
英语单词学习工具 - CLI版本
支持FSRS复习算法、发音、多种学习模式
"""

__version__ = "1.0.0"
__author__ = "Your Name"

from .word_study import WordStudyApp, main

__all__ = ["WordStudyApp", "main"]
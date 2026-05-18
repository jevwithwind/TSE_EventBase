#!/usr/bin/env python3
"""
[OPTIONAL] AI-powered event classification for TSE_EventBase project.

Delegates to classifier_v2/classifier.py, which is a clean rewrite
of the original classifier (see classifier_audit_report.md for details).

Supports any OpenAI-compatible API (OpenAI, Azure, Ollama, Qwen, DeepSeek, etc.).
Set OPENAI_API_KEY, OPENAI_BASE_URL, and MODEL in .env before running.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Make classifier_v2 importable
_CLASSIFIER_V2 = Path(__file__).resolve().parent / "classifier_v2"
if str(_CLASSIFIER_V2) not in sys.path:
    sys.path.insert(0, str(_CLASSIFIER_V2))

from classifier import main as classifier_main

if __name__ == "__main__":
    # Forward all arguments to the new classifier
    sys.argv[0] = str(_CLASSIFIER_V2 / "classifier.py")
    sys.exit(classifier_main())

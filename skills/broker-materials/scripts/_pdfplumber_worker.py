#!/usr/bin/env python3
"""Isolated pdfplumber runner. A hang here is killed by the caller's timeout.
Usage: _pdfplumber_worker.py <pdf_path> <pdf_reader_scripts_dir>"""
import sys
from pathlib import Path
pdf_path = Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
from extract_pdfplumber import extract_with_pdfplumber
text, _ = extract_with_pdfplumber(pdf_path)
sys.stdout.write(text)

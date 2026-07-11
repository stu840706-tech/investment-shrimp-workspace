#!/usr/bin/env python3
"""Inner runner: process_file in an isolated interpreter so the parent
can hard-kill on hang. Progress prints flow to stdout for the parent."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from receive_telegram import load_secrets, process_file

process_file(Path(sys.argv[1]), load_secrets())

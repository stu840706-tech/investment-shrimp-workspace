#!/usr/bin/env python3
import sys, os
sys.path.insert(0, '/home/ubuntu/.openclaw/workspace/workflows')
sys.argv = ['news_aggregator.py', sys.argv[1] if len(sys.argv) > 1 else '07']
exec(open('/home/ubuntu/.openclaw/workspace/workflows/news_aggregator.py').read())
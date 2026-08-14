#!/usr/bin/env python3
"""Operator CLI shim -> callusguard.guard.doctor"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from callusguard.guard.doctor import main  # noqa: E402
sys.exit(main())

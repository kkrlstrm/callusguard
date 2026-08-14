#!/usr/bin/env python3
"""Operator CLI shim -> callusguard.guard.check_rules"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from callusguard.guard.check_rules import main  # noqa: E402
sys.exit(main(sys.argv[1:]))

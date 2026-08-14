#!/usr/bin/env python3
"""Stable hook entry point. Do not edit — Codex trust-pins this file's hash.

Codex records a `trusted_hash` of the hook command in config.toml, so any edit
here re-prompts for trust. All churn belongs in the declaration JSON, which is
not hashed. This file exists only to locate the package and delegate.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from callusguard.wroteonly.runner import run  # noqa: E402

sys.exit(run())

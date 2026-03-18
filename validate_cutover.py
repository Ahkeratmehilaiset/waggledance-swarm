#!/usr/bin/env python3
"""
Cutover Validation — repo-root entry point per spec section 25.

Thin wrapper around waggledance.tools.validate_cutover.
Run: python validate_cutover.py
"""

import sys

from waggledance.tools.validate_cutover import run_validation

if __name__ == "__main__":
    success = run_validation()
    sys.exit(0 if success else 1)

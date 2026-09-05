#!/usr/bin/env python3
"""TraceProof CLI entry point.

Usage:
    python traceproof.py examples/dist_counter/counter.py \\
        --module DistCounter \\
        --desc "Distributed counter with non-atomic increment"
"""
import sys
from harness.pipeline import _cli

if __name__ == "__main__":
    sys.exit(_cli())

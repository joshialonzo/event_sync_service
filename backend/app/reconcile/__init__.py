"""The reconciliation pipeline.

Four independently testable decisions from doc 02, one module each: normalize, dedupe,
match, merge. Everything here is pure — plain models in, plain models out, no I/O and no
framework — which is what lets the correctness fixture run in milliseconds.
"""

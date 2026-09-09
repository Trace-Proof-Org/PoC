"""Trace Validator Agent: validates that the formal TLA+ model conforms to real code traces."""
from agents.trace_validator.validator import validate_trace_conformance

__all__ = ["validate_trace_conformance"]

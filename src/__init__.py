"""Multimodal Workplace Chatbot — reference implementation package.

Implements docs/02-architecture.md. Modules are split so that pure-logic
components (routing, chunking, degradation, config) import and unit-test
without any Azure SDK present; Azure SDKs are imported lazily inside the
functions/classes that need them.
"""

__version__ = "0.1.0"

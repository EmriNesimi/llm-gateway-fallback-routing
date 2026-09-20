"""An LLM gateway.

One endpoint in front of several providers, with fallback between them,
per-key rate limits and budgets, and a hard lifetime spend ceiling.
"""

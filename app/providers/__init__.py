"""One adapter per upstream, all speaking the same shape to the router.

Each takes ChatMessages and SamplingParams and returns a ChatResponse or a
stream of StreamChunks, with token counts the ledgers can price. The
provider-specific spelling of everything — system prompts, sampling
parameters, streaming framing — stays inside its adapter.
"""

"""Spend control: two ledgers, both reserve-then-settle.

`provider_budget` is the operator's money — a lifetime ceiling per upstream
provider that never resets. `tracker` is one caller's monthly share. They
answer different questions and neither substitutes for the other; decisions
011, 015 and 017 say why there are two and which to believe when they
disagree. `pricing` is what both are measured in.
"""

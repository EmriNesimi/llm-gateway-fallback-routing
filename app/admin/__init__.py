"""Key issuance and revocation, and read access to both audit logs.

Outside the /v1 contract (docs/api-versioning.md): operational, not
something a client application is pointed at. Gated on ADMIN_API_KEY and
rate limited before the key check, since this key mints the others.
"""

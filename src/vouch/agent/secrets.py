"""Which paths must never have their contents sent to the model.

Pass 2 is the only part of vouch that leaves the machine. An untracked `.env`
is both exactly the kind of hunk vouch should flag and exactly the kind whose
body must not appear in a prompt, so such files are judged by path alone.

This is a safety floor, not secret detection: it stops the obvious cases by
name. Keeping credentials out of a working tree is still the user's job.
"""

from __future__ import annotations

_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        ".npmrc",
        ".pgpass",
        "credentials",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
        "service-account.json",
    }
)
_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
# `.env.local`, `.env.production`, `id_rsa.bak`, and friends.
_PREFIXES = (".env.", "id_rsa.", "id_ed25519.")


def holds_secrets(path: str) -> bool:
    """Whether a path's contents should be withheld from the model."""
    name = path.rsplit("/", 1)[-1].lower()
    if name.endswith(".example") or name.endswith(".sample"):
        # `.env.example` is documentation; withholding it teaches nothing.
        return False
    return name in _NAMES or name.endswith(_SUFFIXES) or name.startswith(_PREFIXES)


__all__ = ["holds_secrets"]

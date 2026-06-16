"""Password hashing with stdlib scrypt — no external deps."""
from __future__ import annotations

import base64
import hashlib
import secrets

_N = 16384      # CPU/memory cost (2^14)
_R = 8          # block size
_P = 1          # parallelisation
_DKLEN = 32
_SALT_LEN = 16


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_LEN)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_N, r=_R, p=_P, dklen=_DKLEN,
    )
    return (
        f"scrypt${_N}${_R}${_P}$"
        f"{base64.b64encode(salt).decode()}${base64.b64encode(derived).decode()}"
    )


def verify_password(password: str, hashed: str) -> bool:
    try:
        algo, n_s, r_s, p_s, salt_b64, hash_b64 = hashed.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_s), r=int(r_s), p=int(p_s), dklen=len(expected),
        )
        return secrets.compare_digest(derived, expected)
    except Exception:
        return False

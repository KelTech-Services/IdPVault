"""Test env: isolated master key + dummy DB url set BEFORE app imports.
The suite is deliberately DB- and network-free (smoke level)."""
import os
import tempfile

_k = tempfile.NamedTemporaryFile(delete=False)
_k.write(os.urandom(32))
_k.close()
os.environ.setdefault("IDPVAULT_MASTER_KEY_FILE", _k.name)
os.environ.setdefault("IDPVAULT_DATABASE_URL",
                      "postgresql+psycopg://none:none@localhost:1/none")

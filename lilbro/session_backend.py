"""
Database session store that treats unreadable session_data as an empty session.

Django's SessionBase.decode runs base64.b64decode outside its try/except; corrupted
rows in django_session (truncated base64, manual edits, failed writes) then crash
every request using that session cookie. This subclass catches that case.
"""

import binascii
import logging

from django.contrib.sessions.backends.db import SessionStore as DbSessionStore

logger = logging.getLogger(__name__)


class SessionStore(DbSessionStore):
    def decode(self, session_data):
        try:
            return super().decode(session_data)
        except binascii.Error:
            logger.warning(
                "Ignoring session with invalid base64 in session_data; "
                "client will get a fresh session on next save."
            )
            return {}

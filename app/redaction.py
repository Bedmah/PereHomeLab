import os
import re
from urllib.parse import quote


def redact_sensitive(value: str) -> str:
    text = str(value)
    text = re.sub(r'(?i)\b((?:https?|wss?)://)[^/\s@]+@', r'\1[redacted]@', text)
    text = re.sub(r'(?i)(\b(?:bot)?\d{5,16}:)[A-Za-z0-9_-]{20,}', '[redacted-bot-token]', text)
    text = re.sub(r'(?i)(\b(?:Bearer|Basic)\s+)[A-Za-z0-9._~+/=-]+', r'\1[redacted]', text)
    text = re.sub(r'(?i)([?&](?:[^=&\s]*(?:token|password|secret|api_key))=)[^&#\s]+', r'\1[redacted]', text)
    for name, secret in os.environ.items():
        if not secret or not name.endswith(('_TOKEN', '_PASSWORD', '_SECRET', '_ENCRYPTION_KEY')):
            continue
        for candidate in {secret, quote(secret, safe='')}:
            if len(candidate) >= 8:
                text = text.replace(candidate, '[redacted]')
            else:
                text = re.sub(r'(?<!\w)' + re.escape(candidate) + r'(?!\w)', '[redacted]', text)
    return text

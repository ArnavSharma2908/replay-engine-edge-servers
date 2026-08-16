import hashlib
import re
from xml.dom import minidom

HASH_COMMENT_PREFIX = "Content hash: "
_HASH_PATTERN = re.compile(r'\s*Content hash:\s*([a-f0-9]{32})\s*')


def compute_tags_hash(tags: list) -> str:
    """Compute an MD5 hash of the combined tags string (order-preserved)."""
    combined = ''.join(tag.strip() for tag in tags)
    return hashlib.md5(combined.encode('utf-8')).hexdigest()


def find_hash_in_comments(xml_doc: minidom.Document, hash_value: str) -> bool:
    """Return True if a matching content-hash comment already exists in the document."""
    def _walk(node):
        if node.nodeType == node.COMMENT_NODE:
            m = _HASH_PATTERN.match(node.nodeValue)
            if m and m.group(1) == hash_value:
                return True
        for child in node.childNodes:
            if _walk(child):
                return True
        return False

    return _walk(xml_doc)

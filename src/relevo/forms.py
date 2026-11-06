"""Which ATS a link belongs to, and its form."""
from relevo import ashby, breezy, greenhouse, manatal

READERS = {"ashby": ashby, "greenhouse": greenhouse, "manatal": manatal, "breezy": breezy}
NAMES = {"ashby": "Ashby", "greenhouse": "Greenhouse", "manatal": "Manatal", "breezy": "Breezy"}


def find(*texts: str) -> tuple[str, str]:
    """(ats, url) of the first form link found, or ("", "")."""
    for text in texts:
        for ats, reader in READERS.items():
            m = reader.URL.search(text or "")
            if m:
                return ats, m.group(0)
    return "", ""


def fetch(ats: str, url: str):
    return READERS[ats].fetch_form(url)

"""Which ATS a link belongs to, and its form."""
from relevo import ashby, greenhouse

READERS = {"ashby": ashby, "greenhouse": greenhouse}
NAMES = {"ashby": "Ashby", "greenhouse": "Greenhouse"}


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

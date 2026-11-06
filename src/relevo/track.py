"""A code of its own for each application, so a click on the site says which one was opened."""
import re
import secrets
from pathlib import Path

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # the same as the codes made by hand
SITE = re.compile(r"(?<![@\w.-])(?:https?://)?(?:www\.)?joseguzman\.dev/?(?![\w/-])")


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(4))


def in_text(text: str, code: str) -> str:
    return SITE.sub(f"https://joseguzman.dev/r/{code}", text)


def stamp_cv(source: str, folder: Path, code: str) -> str:
    """A copy of the CV whose site link carries the code. The name is the one the company sees."""
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, TextStringObject

    writer = PdfWriter(clone_from=source)
    found = 0
    for page in writer.pages:
        for note in page.get("/Annots", []):
            action = note.get_object().get("/A")
            uri = str(action.get_object().get("/URI", "")) if action else ""
            if "joseguzman.dev/r/" in uri:
                action.get_object()[NameObject("/URI")] = TextStringObject(re.sub(r"/r/\w+", f"/r/{code}", uri))
                found += 1
    if not found:
        raise ValueError("the CV has no site link to mark")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "Jose_Guzman_CV.pdf"
    writer.write(path)
    return str(path)

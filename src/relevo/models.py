from dataclasses import dataclass, field

KINDS = {"text", "long_text", "email", "number", "file", "single_choice", "multi_choice", "yes_no", "location"}


@dataclass(frozen=True)
class Field:
    id: str
    label: str
    kind: str
    required: bool
    options: tuple[str, ...] = ()

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown field kind: {self.kind}")


@dataclass
class Form:
    url: str
    fields: list[Field] = field(default_factory=list)
    ats: str = "ashby"

    def missing(self, answers: dict) -> list[str]:
        """Labels of required fields with no answer."""
        return [f.label for f in self.fields if f.required and answers.get(f.id) in (None, "", [])]

    def invalid(self, answers: dict) -> list[str]:
        """Labels whose answer is not one of the field's options."""
        bad = []
        for f in self.fields:
            value = answers.get(f.id)
            if value in (None, "", []) or not f.options:
                continue
            values = value if isinstance(value, list) else [value]
            if any(v not in f.options for v in values):
                bad.append(f.label)
        return bad

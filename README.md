# Relevo

Vigía finds a job; Relevo takes it from there. It reads the application form, prepares every answer and waits.
Nothing is sent until a person approves that application.

Rules the code keeps:
- Field types and `required` come from the ATS data, never from a model.
- A model reading a page has no personal data. A model drafting has no tools.
- Code fills and submits, with approved values only. `submit=True` stops if any field does not verify.
- No LinkedIn, no logged-in accounts, no CAPTCHA workarounds.

    python3 -m unittest discover -s tests

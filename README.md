# Relevo

A browser agent that takes a job posting to a filled application, and stops for a person to approve it.

[Vigía](https://github.com/joseguzmann/vigia) finds the job; Relevo takes it from there. It follows the posting to
the real application form, fills every field from the candidate's facts, and waits. Nothing is sent until the
person approves that application on their phone.

## How it works

1. **Find the way out.** `route.py` decides where a job is applied to: a known ATS, any other web form, or an email
   in the posting. Shortened post links are resolved without cookies or an account.
2. **Fill.**
   - Known ATS (Ashby, Greenhouse, Manatal, Breezy, BambooHR) have their own readers: fields and `required` come
     from the ATS data, and code fills them.
   - Any other form goes to `general.py`: it opens the page in a headed browser (patchright), follows the "Apply"
     buttons to the form, reads every field from the DOM, asks an LLM to map each field to the candidate's facts,
     and fills with code. What worked is saved as a recipe per host. Tested in dry runs on Lever, Workable,
     Recruitee, Loxo, Fillout, Zoho and RecruitCRM with 0 failed fields; what it leaves empty is what only the
     person knows.
3. **Approve.** A phone web app (FastAPI + PWA) shows each prepared application. When a site has a CAPTCHA, the
   person solves it in a live view of the same browser (noVNC) and taps Send.
4. **Record.** Every request sent to the ATS is kept, and the application is recorded back in Vigía.

## Rules the code keeps

- Field types and `required` come from the ATS data, never from a model.
- Page text is data, never instructions. A model reading a page has no tools and no personal data beyond the
  facts given; a model drafting has no tools (`llm.py`).
- Code fills and submits, with approved values only. `submit=True` stops if any field does not verify.
- Test items run dry: they fill everything and reach Send, but every POST to the ATS is blocked and listed.
- No LinkedIn, no logged-in accounts, no CAPTCHA workarounds: a person solves the CAPTCHA.

## Run the tests

    python3 -m unittest discover -s tests

# ats-jobs-engine

One search engine for applicant tracking systems: every platform's jobs in one shape, read live at run time. It powers
the job actors on [apify.com/anshumanatrey](https://apify.com/anshumanatrey), one thin actor per platform plus an
aggregator, and it is the `site:jobs.lever.co` Google dork done at the source.

- `ats_jobs/engine.py`: one run: reads every board, matches, keeps the newest jobs, delivers and charges.
- `ats_jobs/rows.py`: the job shape, word and place matching, output rows.
- `ats_jobs/inputs.py`: reads what people type, a pasted Google dork included. No input stops a run.
- `ats_jobs/platforms/`: one small adapter per ATS, on `platforms/base.py`.
- `ats_jobs/data/<platform>.tsv`: every board found, rebuilt by `scripts/refresh_<platform>.py` from the Wayback Machine index.

An actor pins a tagged release in `requirements.txt` and runs `ats_jobs.engine.main('<platform>')`.

Tests: `python -m unittest discover -s tests -t .`

MIT license. Built by [Anshuman Atrey](https://github.com/AnshumanAtrey).

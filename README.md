# ats-jobs-engine

One search engine for applicant tracking systems: every platform's jobs in one shape, read live at run time. It powers
the job actors on [apify.com/anshumanatrey](https://apify.com/anshumanatrey), one thin actor per platform plus an
aggregator, and it is the `site:jobs.lever.co` Google dork done at the source.

- `ats_jobs/engine.py`: one run: reads every board, matches, keeps the newest jobs, delivers and charges.
- `ats_jobs/rows.py`: the job shape, word and place matching, output rows.
- `ats_jobs/inputs.py`: reads what people type, a pasted Google dork included. No input stops a run.
- `ats_jobs/platforms/`: one small adapter per platform, on `platforms/base.py`: the ATSs (Greenhouse, Lever, Workday,
  SmartRecruiters, Workable, Cornerstone, Deel), India's job boards (Naukri, foundit, Instahyre, Hirist, Cutshort) and
  global job boards (Y Combinator's Work at a Startup; Welcome to the Jungle in Europe and the US, with the UK site
  that was Otta; eFinancialCareers, finance, banking and quant jobs in every country it serves).
- `ats_jobs/data/<platform>.tsv`: every board found, rebuilt by `scripts/refresh_<platform>.py` from the Wayback Machine index.
  A job board that searches its own index has no board list: its sites are in its adapter, and a data file holds the
  site's own places, companies or sectors (`foundit_places.tsv`, `naukri-cities.tsv`, `instahyre.tsv`,
  `welcometothejungle_places.tsv`, `welcometothejungle_industries.tsv`, `efinancialcareers.tsv`), rebuilt by its script.
- Shared filters beyond the ATSs': years of experience (`experienceYears`) for every job board; a platform's own
  filters live in its adapter (`Platform.fits`), and a run that finds nothing names the one that left jobs out. A
  platform can add job types of its own (`Platform.job_types`: work-study on Welcome to the Jungle) and the place names
  its search box reads (`Platform.place_names`).
- Instahyre's job pages need a browser's TLS: install the `instahyre` extra (`curl_cffi`).

An actor pins a tagged release in `requirements.txt` and runs `ats_jobs.engine.main('<platform>')`.

Tests: `python -m unittest discover -s tests -t .`

MIT license. Built by [Anshuman Atrey](https://github.com/AnshumanAtrey).

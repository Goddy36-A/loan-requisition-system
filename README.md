# Loan Requisition System (Windows, Django, SQLite)

Run: `python setup_and_run.py` then open http://127.0.0.1:8000/ (admin at /admin/).
Options: `--no-seed`, `--reset-db`, `--install-expiration`, `--disable-expiration`, `--expiration-status`, `--test-expiration`.
Tests: `python manage.py test`.

## Sign-in accounts on this computer

Setup creates these accounts so you can try every role straight away. They are also listed on the sign-in page, and only while
the app runs on this computer: admin/Admin@12345, customer1/Customer@12345, loanofficer/LoanOfficer@12345,
creditofficer/CreditOfficer@12345, manager/Manager@12345, researcher/Researcher@12345.
Forgot one, or changed it and lost it? `python manage.py seed_data --reset-passwords` puts them back.

These passwords are public (they are in this file), so they must never be used on a server. See "Hosting in the cloud" below:
the app refuses to start there while any of them is still active.

Expiration: off by default. Task `LoanRequisitionResearch_Autophagy` only exists after `--install-expiration`; deletion also needs
`DELETE_PROJECT = True` in autophagy.py. Remove with `--disable-expiration`. If the project folder is already gone, delete the task
manually in Windows Task Scheduler.

Implemented: registration and sign-in with lockout, roles enforced server-side, loan products, apply/draft/submit, strict status
workflow, documents (upload, review, protected download), credit assessment, manager decision, disbursement and repayments with a
late-payment penalty, account management (bank accounts, next of kin, witnesses, password change), notifications, audit trail,
questionnaire, analytics, admin.
Not yet implemented: REST API, search/filter/pagination, password reset by email, USER_MANUAL.md, ARCHITECTURE.md.

## Repayments

Workflow after approval: `APPROVED` -> `DISBURSED` (loan officer, with a disbursement reference) -> `COMPLETED` (automatic when the
last cent is paid). The repayment schedule is created at disbursement, so due dates run from the day the money is paid out.

- **Recording payments:** loan officers record payments on the application page (cash, simulated mobile money or bank). Money is
  applied to the oldest installment first, late fee before installment. Payments may run ahead of the schedule but may not exceed
  the outstanding balance. Every payment is audited and the customer gets a notification and a simulated SMS.
- **Late payment penalty:** a one-off fee of `LATE_FEE_PERCENT` (default 5%) of the unpaid installment, charged once when it is more
  than `LATE_FEE_GRACE_DAYS` (default 3) past its due date. It does not compound. Lateness is judged as of the payment date, so an
  on-time payment that is recorded late is not penalised. Change the values in `.env`.
- **Early settlement:** pay the full outstanding balance (shown on the application page). Interest is not recalculated.
- **Overdue list:** `/overdue/` for loan officers, branch managers and admins; the research analytics page shows the loan book.
- **Scheduled jobs:** late fees are also assessed whenever an application page or the overdue list is opened. To keep fees and
  reminders current without anyone opening pages, run once a day: `python manage.py repayment_jobs` (safe to run repeatedly; it
  assesses late fees and sends payment reminders `REMINDER_DAYS_BEFORE` days ahead, once per installment).
- Payments cannot be added or deleted in the Django admin, so installments always stay in sync with the payment history.

After pulling this change run `python manage.py migrate`. Loans approved before this change stay `APPROVED` until a loan officer
disburses them; their schedule is rebuilt from the disbursement date.

## Accounts, next of kin and witnesses

Customers manage their details under **My account** (`/account/`): phone and National ID, bank accounts, next of kin and password.

- **Bank account required.** A customer cannot start an application without at least one bank account. Each application names the
  account the loan is paid into (their primary account if they do not choose). Account numbers are shown masked to the customer
  and in full only to staff. Removing an account archives it, and is refused while a loan using it is in progress or being repaid.
- **Next of kin** (up to 5) belong to the customer's account. At least one is needed to submit, and the last one cannot be
  removed while a loan is in progress.
- **Witnesses** belong to one application. At least `MIN_WITNESSES` (default 1, at most `MAX_WITNESSES`, default 3) are needed to
  submit. A witness cannot be the applicant or the guarantor, and the same person cannot be added twice. Witnesses can be changed
  while the application is a draft or waiting for more information.
- Phone numbers are cleaned up and stored as `+256...`; account numbers must be 6 to 20 digits; NINs must be unique per account.
- Disbursement records which account the money went to in the audit trail; it is refused if the customer has no active account.

Existing customers keep working: after `python manage.py migrate` they simply need to add a bank account and a next of kin before
their next submission. Applications already submitted are not affected. The sample customer (`customer1`) comes with both.

## Works without internet

The website needs no internet connection. Bootstrap 5.3.3 (MIT licence) is bundled in `core/static/core/vendor/bootstrap/`, fonts
are the system's own, and no page loads anything from another host. The only time internet is needed is the very first run of
`setup_and_run.py`, to download Django. After that the script works offline (if the network is down it carries on with what is
already installed). A test (`tests/test_offline.py`) fails if a CDN link is ever added back.

Note for later: bundled files are served by Django's development server. When the app is hosted with `DJANGO_DEBUG=0`, static
files need a production setup (for example WhiteNoise plus `collectstatic`).

## Hosting in the cloud

The app runs in one of two modes, chosen automatically:

- **Local mode** (the default): it is only reachable from this computer. You get the one-command setup, the sample sign-ins above,
  and gentle sign-in limits.
- **Hosted mode**: as soon as `DJANGO_ALLOWED_HOSTS` lists a real host name, or `DJANGO_DEBUG=0`. Debug pages are refused, a real
  secret key is required, cookies are marked secure, HTTPS is enforced, static files are served by WhiteNoise, and sign-in limits
  are stricter.

To host it:

1. `pip install -r requirements-cloud.txt` (adds WhiteNoise and gunicorn).
2. Set the environment variables (see `.env.example`):
   `DJANGO_ALLOWED_HOSTS=myapp.example.com`, `DJANGO_SECRET_KEY=<long random value>`,
   `DJANGO_CSRF_TRUSTED_ORIGINS=https://myapp.example.com`, and `DJANGO_BEHIND_PROXY=1` if the host puts a proxy in front of the
   app (most do). Generate a key with `python -c "import secrets; print(secrets.token_urlsafe(50))"`.
3. `python manage.py migrate`, then `python manage.py collectstatic --noinput`.
4. `python manage.py seed_data`. In hosted mode this gives the sample accounts **random passwords, printed once** (or the ones you
   chose with `SEED_PASSWORD_ADMIN`, `SEED_PASSWORD_MANAGER`, ...). Save them, and change them after signing in.
5. Start with `gunicorn config.wsgi`.

Safety net: if a hosted app finds any sample account still using its published password, `runserver`, `check` and gunicorn all
refuse to start (error `core.E001`) and print the fix: `python manage.py seed_data --reset-passwords`. `migrate` is exempt so an
older database can catch up first.

Sign-in limits: after `LOGIN_MAX_FAILURES` wrong passwords (default 10 on this computer, 5 when hosted) a username is locked for
`LOGIN_LOCK_MINUTES` (1, or 15 when hosted), including on `/admin/`. Wrong attempts for unknown usernames count the same, so the
page never reveals which usernames exist. Unlock someone with the admin page "Login attempts" or
`python manage.py unlock_logins [username]`.

Things still to do before real use: the SQLite file and uploaded documents live on the server's disk, and many hosts erase the disk
on every deploy, so use a persistent disk or move to a hosted database. Use HTTPS (`DJANGO_ALLOW_HTTP=1` is for short trials only),
and raise `DJANGO_HSTS_SECONDS` once HTTPS is proven.

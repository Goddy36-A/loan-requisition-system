# Loan Requisition Research Prototype (Windows, Django, SQLite)
RESEARCH PROTOTYPE - LOCAL TESTING ONLY. Not an official Equity Bank system; all data is fictional.

Run: `python setup_and_run.py` then open http://127.0.0.1:8000/ (admin at /admin/).
Options: `--no-seed`, `--reset-db`, `--install-expiration`, `--disable-expiration`, `--expiration-status`, `--test-expiration`.
Tests: `python manage.py test`.

Demo accounts (LOCAL DEVELOPMENT / RESEARCH ONLY): admin/Admin@12345, customer1/Customer@12345, loanofficer/LoanOfficer@12345,
creditofficer/CreditOfficer@12345, manager/Manager@12345, researcher/Researcher@12345.

Expiration: off by default. Task `LoanRequisitionResearch_Autophagy` only exists after `--install-expiration`; deletion also needs
`DELETE_PROJECT = True` in autophagy.py. Remove with `--disable-expiration`. If the project folder is already gone, delete the task
manually in Windows Task Scheduler.

Implemented: auth/register, roles enforced server-side, products, apply/draft/submit, strict status workflow, credit assessment,
manager decision, notifications (model + dashboard), audit trail, questionnaire, basic analytics, admin.
NOT yet implemented: document upload/protected download views, REST API, CSV export, password change/reset, search/filter,
login throttling, USER_MANUAL.md, ARCHITECTURE.md.

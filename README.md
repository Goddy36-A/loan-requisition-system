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

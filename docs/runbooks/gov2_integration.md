# GOV2.0-RC1 production integration runbook

Management decisions of 13 Oct 2026. Run on the backend VM from
`/home/azureuser/meritlense`. Every step lists how to check it before moving on.
Real customer assessments start only after management reviews the closure
report and confirms launch.

## 0. Load the database settings

```bash
cd /home/azureuser/meritlense
set -a; . ./.env; set +a
export PGPASSWORD="$DB_PASSWORD" PGHOST="$DB_HOST" PGPORT="${DB_PORT:-5432}" PGUSER="$DB_USER"
mkdir -p ~/backups
STAMP=$(date +%Y%m%d-%H%M)
```

## 1. Back up and verify (before any change)

```bash
pg_dump -Fc -f ~/backups/meritlense-$STAMP.dump "$DB_NAME"
sha256sum ~/backups/meritlense-$STAMP.dump > ~/backups/meritlense-$STAMP.dump.sha256
pg_restore --list ~/backups/meritlense-$STAMP.dump | grep -c "TABLE DATA"   # must be > 0
```

Verify by restoring into a scratch database and comparing row counts:

```bash
createdb meritlense_restore_check
pg_restore --no-owner -d meritlense_restore_check ~/backups/meritlense-$STAMP.dump
for DB in "$DB_NAME" meritlense_restore_check; do
  echo "== $DB"
  psql -d "$DB" -At -c "
    SELECT 'users', count(*) FROM accounts_user UNION ALL
    SELECT 'companies', count(*) FROM accounts_company UNION ALL
    SELECT 'candidates', count(*) FROM candidates_candidate UNION ALL
    SELECT 'sessions', count(*) FROM interview_sessions_interviewsession UNION ALL
    SELECT 'evaluations', count(*) FROM evaluations_evaluation UNION ALL
    SELECT 'readiness_records', count(*) FROM evaluations_evaluationreadinessdecisionrecord UNION ALL
    SELECT 'reports', count(*) FROM reports_evaluationreport UNION ALL
    SELECT 'certificates', count(*) FROM evaluations_certificate UNION ALL
    SELECT 'audit_logs', count(*) FROM audit_auditlog UNION ALL
    SELECT 'subscriptions', count(*) FROM payments_subscription UNION ALL
    SELECT 'question_templates', count(*) FROM questions_questiontemplate;"
done
dropdb meritlense_restore_check
```

The two blocks must match. If `createdb` is not permitted for this user, keep
the `pg_restore --list` check and say so in the report. Copy the dump and its
`.sha256` off the VM.

## 2. Deploy

Merge backend PR #191 (deploy runs migrations and restarts gunicorn), then
frontend PRs #202 and #201. Watch both GitHub Actions runs to completion.

```bash
python manage.py showmigrations evaluations interviews | tail -5   # 0028 / 0006 applied
```

## 3. Confirm which records are test-only

```bash
python manage.py trial_data_inventory --json ~/backups/inventory-$STAMP.json
```

Send the inventory to management. They return the confirmed list of
test-account emails as `~/backups/test_accounts.json` (a JSON list).

## 4. Clear trial assessment data

```bash
python manage.py clear_trial_data --accounts ~/backups/test_accounts.json        # dry run: note the evaluation count
python manage.py clear_trial_data --accounts ~/backups/test_accounts.json --execute \
    --backup-file ~/backups/meritlense-$STAMP.dump --confirm-evaluations <count from the dry run>
```

Accounts, companies, subscriptions, payments, configuration, permissions and
audit logs are never deleted. An audit entry records what was cleared.

## 5. Load and activate the bank

```bash
python manage.py import_question_bank_draft            # inactive: 4,200 templates, 42 rule sets
python manage.py activate_question_bank_version GOV2.0-RC1 --dry-run
python manage.py activate_question_bank_version GOV2.0-RC1
```

Rollback, if needed:

```bash
python manage.py activate_question_bank_version GOV2.0-RC1 --rollback
```

Assessments already started keep their own version either way.

## 6. End-to-end validation (English and Arabic)

Use the test accounts kept for validation. For each language, run a Full and a
Screening assessment for at least one role per sector. Check:

- the answers are interpreted;
- the evidence is extracted;
- the score is calculated;
- the readiness decision is made;
- the report is produced;
- certificate eligibility is decided;
- held results appear in Admin > Review Queue and can be released.

Then clear the validation data again with `clear_trial_data` before launch.

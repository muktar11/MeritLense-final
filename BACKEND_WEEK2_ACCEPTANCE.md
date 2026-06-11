# Backend Week 2 Acceptance

## Scope

Week 2 is complete when the backend reliably supports:

- JWT login and refresh
- Registration and email verification
- Forgot password, reset password, and change password
- Profile retrieval and profile update
- Role-based access for admin and employer flows
- Candidate create, list, retrieve, update, delete
- Candidate share and unshare for company teams

## Acceptance Checklist

- `POST /api/v1/auth/register/b2c/` creates a B2C user and profile.
- `POST /api/v1/auth/register/b2b/` creates a B2B user, company, and employer profile.
- `POST /api/v1/auth/verify-email/` verifies a user email.
- `POST /api/v1/auth/login/` rejects unverified users and returns JWTs for verified users.
- `POST /api/v1/auth/refresh/` returns a fresh access token for a valid refresh token.
- `POST /api/v1/auth/forgot-password/` generates a reset token when the account exists.
- `POST /api/v1/auth/validate-reset-token/` validates reset tokens.
- `POST /api/v1/auth/reset-password/` resets the password and invalidates the token.
- `POST /api/v1/auth/change-password/` changes the password for an authenticated user.
- `GET /api/v1/auth/me/` returns the caller profile for the authenticated role.
- `PATCH /api/v1/auth/me/` updates allowed profile fields for the authenticated role.
- Admin document-review endpoints honor JSON-based `admin_permissions`.
- `POST /api/v1/candidates/candidates/` creates a candidate in the correct ownership scope.
- Candidate duplicate checks are enforced within the caller scope.
- Shared candidates are readable by team members but not editable unless they created them.
- `POST /api/v1/candidates/candidates/{id}/share/` and `/unshare/` accept current team member identifiers safely.

## Automated Coverage

Run the Week 2 backend suite with:

```bash
./.venv/bin/python manage.py test api.accounts api.candidates --settings=meritlense.settings.test
```

The test settings use SQLite and local-memory email so the suite can run without external services.

## Frontend Notes

- Login now requires verified email status.
- Candidate share and unshare accept team member identifiers from the same company and remain backward-compatible with existing team member profile IDs.
- Candidate list visibility is scoped by role:
  - `B2C`: own candidates only
  - `B2B`: company candidates
  - `B2B_TEAM_MEMBER`: candidates created by the member or explicitly shared with the member
  - `ADMIN` and `SUPERADMIN`: all candidates

## API Usage Examples

### Register a B2C user

```bash
curl -X POST http://localhost:8000/api/v1/auth/register/b2c \
  -F "email=new-b2c@example.com" \
  -F "first_name=Sara" \
  -F "last_name=Ahmed" \
  -F "password=Password123!" \
  -F "confirm_password=Password123!" \
  -F "passport_id=REG-1001" \
  -F "job_role=SE" \
  -F "nationality=US" \
  -F "preferred_language=EN" \
  -F "phone_number=+251911223344" \
  -F "date_of_birth=1993-04-05" \
  -F "address=Addis Ababa" \
  -F "id_document=@/path/to/id.pdf" \
  -F "resume_document=@/path/to/resume.pdf"
```

### Register a B2B user

```bash
curl -X POST http://localhost:8000/api/v1/auth/register/b2b \
  -F "email=company-admin@example.com" \
  -F "first_name=John" \
  -F "last_name=Smith" \
  -F "password=Password123!" \
  -F "confirm_password=Password123!" \
  -F "company_name=ABC Solutions Ltd" \
  -F "company_registration_number=BIZ-2024-001" \
  -F "company_size=1-10" \
  -F "country=Ethiopia" \
  -F "city=Addis Ababa" \
  -F "preferred_language=EN" \
  -F "phone_number=+251922334455" \
  -F "website=https://abcsolutions.example.com" \
  -F "industry=Technology" \
  -F "address=Bole, Addis Ababa" \
  -F "registration_certificate=@/path/to/certificate.pdf" \
  -F "resachetified_license=@/path/to/license.pdf" \
  -F "tax_id_document=@/path/to/tax.pdf"
```

### Verify email and log in

```bash
curl -X POST http://localhost:8000/api/v1/auth/verify-email \
  -H "Content-Type: application/json" \
  -d '{"email":"company-admin@example.com","code":"12345"}'

curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"company-admin@example.com","password":"Password123!"}'
```

### Fetch and update profile

```bash
curl -X GET http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer <access_token>"

curl -X PATCH http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"first_name":"Updated","last_name":"Name","phone_number":"+251911000000"}'
```

### Upload a profile document

```bash
curl -X POST http://localhost:8000/api/v1/auth/documents/upload \
  -H "Authorization: Bearer <access_token>" \
  -F "document_type=tax" \
  -F "document=@/path/to/tax.pdf"
```

### Create and share a candidate

```bash
curl -X POST http://localhost:8000/api/v1/candidates/candidates \
  -H "Authorization: Bearer <access_token>" \
  -F "first_name=Jane" \
  -F "last_name=Doe" \
  -F "email=jane.doe@example.com" \
  -F "passport_id=PASS-1001" \
  -F "job_role=NA" \
  -F "core_skills=communication,patience" \
  -F "preferred_language=EN" \
  -F "passport_document=@/path/to/passport.pdf"

curl -X POST http://localhost:8000/api/v1/candidates/candidates/<candidate_public_id>/share \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"user_ids":[12]}'
```

## Recommended Sign-Off Command

Run the Week 2 backend checks with:

```bash
DJANGO_SETTINGS_MODULE=meritlense.settings.test ./.venv/bin/python manage.py test api.accounts api.candidates api.core.tests_public_ids
```

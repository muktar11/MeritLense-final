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

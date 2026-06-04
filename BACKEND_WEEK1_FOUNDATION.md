# Backend Week 1 Foundation

This repo keeps the current `meritlense/` and `api/` layout to avoid risky file moves.

Implemented foundation:

- Environment template: `.env.example`
- Versioned API prefix: `/api/v1/`
- Health check: `/api/v1/health/`
- Swagger docs: `/api/v1/docs/`
- PostgreSQL env configuration
- Azure queue/storage env placeholders for deployment jobs and media
- Base app landing zones: organizations, interviews, questions, translation, monitoring, storage

Azure note:

- Do not add Redis/Celery infrastructure for Week 1.
- Use Azure Queue/Storage for deployment-time background job and media integration when those services are implemented.

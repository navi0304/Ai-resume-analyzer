# ResumeForge AI - Storage + Deployment Guide

## 1) Where data and logins are stored

This app uses SQLite by default.

- Database file path: `resume_ai.db` in the project root.
- In code: `DATABASE_PATH = os.path.join(BASE_DIR, "resume_ai.db")` in `app.py`.

### Tables used

- `users`: candidate/admin accounts.
  - Passwords are stored as secure hashes (`generate_password_hash`), not plain text.
- `jobs`: job posts created by admin.
- `applications`: submitted resumes, status, score, skills, missing skills, recruiter notes.

### Session/login storage

- Login session is stored in Flask's signed cookie (`session["user_id"]`) in browser.
- Cookie integrity uses `SECRET_KEY`.

## 2) Decision emails (shortlisted/rejected)

When admin changes candidate status to:

- `shortlisted` -> sends shortlist email
- `rejected` -> sends rejection email

Email is sent only if SMTP env vars are configured:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`

Use `.env.example` as template.

## 3) Deploy as website (recommended: Render)

### A) Prepare project

1. Ensure files exist:
   - `app.py`
   - `requirements.txt`
2. Add a Procfile (for web startup):

```txt
web: gunicorn app:app
```

3. Add gunicorn dependency to requirements:

```txt
gunicorn>=22.0
```

### B) Create Render Web Service

1. Push project to GitHub.
2. In Render, create **New Web Service** from your repo.
3. Build command:

```bash
pip install -r requirements.txt
```

4. Start command:

```bash
gunicorn app:app
```

5. Set Environment Variables in Render dashboard:

- `SECRET_KEY`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`
- `PUBLIC_BASE_URL`

### C) Important production note

SQLite on ephemeral disks can lose data on restart/deploy in many cloud setups.
For production reliability, migrate to PostgreSQL.

- Keep SQLite for local testing.
- Use managed Postgres in production and update DB connection logic.

## 4) Quick SMTP setup for Gmail

1. Enable 2-step verification on your Gmail account.
2. Create an App Password.
3. Use:
   - `SMTP_HOST=smtp.gmail.com`
   - `SMTP_PORT=587`
   - `SMTP_USER=your-gmail`
   - `SMTP_PASSWORD=app-password`
   - `SMTP_FROM_EMAIL=your-gmail`

## 5) Admin workflow after deploy

1. Login as admin.
2. Create a job from Admin Dashboard.
3. Open job candidates list.
4. Set candidate status and Save.
5. If status is shortlisted/rejected, email is sent automatically.

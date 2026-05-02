from __future__ import annotations

import json
import os
import smtplib
import sqlite3
import ssl
from datetime import datetime
from email.message import EmailMessage
from functools import wraps

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from model import rank_resumes
from parser import extract_text
from skills import extract_missing_skills, extract_skills

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "resume_ai.db")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-in-production")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB per request
app.config["SMTP_HOST"] = os.environ.get("SMTP_HOST", "")
app.config["SMTP_PORT"] = int(os.environ.get("SMTP_PORT", "587"))
app.config["SMTP_USER"] = os.environ.get("SMTP_USER", "")
app.config["SMTP_PASSWORD"] = os.environ.get("SMTP_PASSWORD", "")
app.config["SMTP_FROM_EMAIL"] = os.environ.get("SMTP_FROM_EMAIL", app.config["SMTP_USER"] or "")
app.config["PUBLIC_BASE_URL"] = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:5000")

STATUS_CHOICES = ("submitted", "reviewing", "shortlisted", "rejected")
DEFAULT_ADMIN_EMAIL = "adminanjali@gmail.com"
DEFAULT_ADMIN_PASSWORD = "Admin@Anjali"


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_error: Exception | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            department TEXT NOT NULL,
            location TEXT NOT NULL,
            employment_type TEXT NOT NULL,
            description TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            resume_filename TEXT NOT NULL,
            resume_text TEXT NOT NULL,
            extracted_skills TEXT NOT NULL DEFAULT '[]',
            score REAL,
            missing_skills TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'reviewing', 'shortlisted', 'rejected')),
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(job_id, user_id),
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )
    db.commit()
    ensure_seed_admin(db)


def ensure_seed_admin(db: sqlite3.Connection) -> None:
    admin_email = os.environ.get("ADMIN_EMAIL", DEFAULT_ADMIN_EMAIL).strip().lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)
    legacy_default_email = "admin@resumeforge.ai"

    exists = db.execute("SELECT id FROM users WHERE email = ?", (admin_email,)).fetchone()
    if exists:
        return

    # Smooth upgrade path: convert legacy seeded admin into configured admin.
    legacy = db.execute("SELECT id FROM users WHERE email = ?", (legacy_default_email,)).fetchone()
    if legacy and admin_email != legacy_default_email:
        db.execute(
            "UPDATE users SET email = ?, password_hash = ? WHERE id = ?",
            (admin_email, generate_password_hash(admin_password), legacy["id"]),
        )
        db.commit()
        return

    db.execute(
        """
        INSERT INTO users (name, email, password_hash, role, created_at)
        VALUES (?, ?, ?, 'admin', ?)
        """,
        (
            "Platform Admin",
            admin_email,
            generate_password_hash(admin_password),
            utcnow_iso(),
        ),
    )
    db.commit()


def utcnow_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def to_json_list(items: list[str]) -> str:
    return json.dumps(sorted(set(items)), ensure_ascii=True)


def from_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return [str(item) for item in data]
    except json.JSONDecodeError:
        pass
    return []


def extract_score_value(raw_score: object) -> float:
    if raw_score is None:
        return 0.0

    if hasattr(raw_score, "__len__") and not isinstance(raw_score, (str, bytes)):
        try:
            if len(raw_score) > 0:  # type: ignore[arg-type]
                return float(raw_score[0])  # type: ignore[index]
        except Exception:
            pass

    return float(raw_score)


def smtp_is_configured() -> bool:
    return bool(
        app.config["SMTP_HOST"]
        and app.config["SMTP_PORT"]
        and app.config["SMTP_USER"]
        and app.config["SMTP_PASSWORD"]
        and app.config["SMTP_FROM_EMAIL"]
    )


def send_decision_email(
    candidate_email: str,
    candidate_name: str,
    job_title: str,
    status: str,
    notes: str,
) -> tuple[bool, str]:
    if status not in {"shortlisted", "rejected"}:
        return False, "No email rule for this status."

    if not smtp_is_configured():
        return False, "SMTP is not configured. Add SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM_EMAIL."

    decision_label = "Shortlisted" if status == "shortlisted" else "Rejected"
    subject = f"Application Update: {job_title} - {decision_label}"

    lines = [
        f"Hi {candidate_name},",
        "",
    ]

    if status == "shortlisted":
        lines.append(f"Great news! You have been shortlisted for the role: {job_title}.")
        lines.append("Our team will contact you with the next steps soon.")
    else:
        lines.append(f"Thank you for applying for {job_title}.")
        lines.append("After review, we are not moving forward with your application at this stage.")

    if notes:
        lines.extend(["", "Recruiter note:", notes])

    lines.extend(["", "Regards,", "Recruitment Team"])

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = app.config["SMTP_FROM_EMAIL"]
    msg["To"] = candidate_email
    msg.set_content("\n".join(lines))

    host = app.config["SMTP_HOST"]
    port = int(app.config["SMTP_PORT"])
    user = app.config["SMTP_USER"]
    password = app.config["SMTP_PASSWORD"]

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(user, password)
                smtp.send_message(msg)
    except Exception as exc:
        return False, str(exc)

    return True, "Decision email sent."


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


def role_required(required_role: str):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if g.user is None:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login", next=request.path))

            if g.user["role"] != required_role:
                flash("You do not have permission to open that page.", "danger")
                if g.user["role"] == "admin":
                    return redirect(url_for("admin_dashboard"))
                return redirect(url_for("user_dashboard"))

            return view_func(*args, **kwargs)

        return wrapped

    return decorator


@app.before_request
def load_logged_in_user() -> None:
    g.user = None
    user_id = session.get("user_id")
    if user_id is None:
        return

    db = get_db()
    g.user = db.execute(
        "SELECT id, name, email, role FROM users WHERE id = ?", (user_id,)
    ).fetchone()


@app.context_processor
def inject_globals() -> dict[str, object]:
    return {
        "current_user": g.user,
        "current_year": datetime.now().year,
        "status_choices": STATUS_CHOICES,
    }


@app.route("/")
def landing() -> str:
    db = get_db()
    jobs = db.execute(
        """
        SELECT id, title, department, location, employment_type, created_at
        FROM jobs
        WHERE is_active = 1
        ORDER BY created_at DESC
        LIMIT 6
        """
    ).fetchall()

    stats = db.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM jobs WHERE is_active = 1) AS open_roles,
            (SELECT COUNT(*) FROM applications) AS total_applications,
            (SELECT COUNT(*) FROM users WHERE role = 'user') AS registered_talent
        """
    ).fetchone()

    return render_template("landing.html", jobs=jobs, stats=stats)


@app.route("/signup", methods=["GET", "POST"])
def signup() -> str:
    if g.user is not None:
        return redirect(url_for("admin_dashboard" if g.user["role"] == "admin" else "user_dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if len(name) < 2:
            flash("Please enter your full name.", "danger")
            return render_template("auth.html", mode="signup")

        if "@" not in email:
            flash("Please enter a valid email address.", "danger")
            return render_template("auth.html", mode="signup")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("auth.html", mode="signup")

        db = get_db()
        try:
            db.execute(
                """
                INSERT INTO users (name, email, password_hash, role, created_at)
                VALUES (?, ?, ?, 'user', ?)
                """,
                (name, email, generate_password_hash(password), utcnow_iso()),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("That email is already registered. Please log in.", "warning")
            return redirect(url_for("login"))

        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("auth.html", mode="signup")


@app.route("/login", methods=["GET", "POST"])
def login() -> str:
    if g.user is not None:
        return redirect(url_for("admin_dashboard" if g.user["role"] == "admin" else "user_dashboard"))

    next_url = request.args.get("next", "")

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        next_url = request.form.get("next", "")

        db = get_db()
        user = db.execute(
            "SELECT id, name, email, role, password_hash FROM users WHERE email = ?", (email,)
        ).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.", "danger")
            return render_template("auth.html", mode="login", next_url=next_url)

        session.clear()
        session["user_id"] = user["id"]

        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)

        if user["role"] == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("user_dashboard"))

    return render_template("auth.html", mode="login", next_url=next_url)


@app.route("/logout")
def logout() -> str:
    session.clear()
    flash("You are now logged out.", "info")
    return redirect(url_for("landing"))


@app.route("/jobs")
@role_required("user")
def browse_jobs() -> str:
    query = request.args.get("q", "").strip().lower()
    db = get_db()

    if query:
        jobs = db.execute(
            """
            SELECT j.*, a.id AS application_id
            FROM jobs j
            LEFT JOIN applications a
              ON a.job_id = j.id AND a.user_id = ?
            WHERE j.is_active = 1
              AND (LOWER(j.title) LIKE ? OR LOWER(j.location) LIKE ? OR LOWER(j.department) LIKE ?)
            ORDER BY j.created_at DESC
            """,
            (g.user["id"], f"%{query}%", f"%{query}%", f"%{query}%"),
        ).fetchall()
    else:
        jobs = db.execute(
            """
            SELECT j.*, a.id AS application_id
            FROM jobs j
            LEFT JOIN applications a
              ON a.job_id = j.id AND a.user_id = ?
            WHERE j.is_active = 1
            ORDER BY j.created_at DESC
            """,
            (g.user["id"],),
        ).fetchall()

    return render_template("user_dashboard.html", jobs=jobs, query=query)


@app.route("/user/dashboard")
@role_required("user")
def user_dashboard() -> str:
    db = get_db()

    jobs = db.execute(
        """
        SELECT j.*, a.id AS application_id
        FROM jobs j
        LEFT JOIN applications a
          ON a.job_id = j.id AND a.user_id = ?
        WHERE j.is_active = 1
        ORDER BY j.created_at DESC
        """,
        (g.user["id"],),
    ).fetchall()

    application_rows = db.execute(
        """
        SELECT a.id, a.status, a.score, a.extracted_skills, a.missing_skills,
               a.created_at, j.title, j.department
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        WHERE a.user_id = ?
        ORDER BY a.created_at DESC
        """,
        (g.user["id"],),
    ).fetchall()

    applications = []
    for row in application_rows:
        record = dict(row)
        record["skills"] = from_json_list(record.pop("extracted_skills", "[]"))
        record["missing"] = from_json_list(record.pop("missing_skills", "[]"))
        applications.append(record)

    summary = {
        "total": len(applications),
        "shortlisted": sum(1 for item in applications if item["status"] == "shortlisted"),
        "pending": sum(1 for item in applications if item["status"] in {"submitted", "reviewing"}),
    }

    return render_template(
        "user_dashboard.html",
        jobs=jobs,
        applications=applications,
        summary=summary,
        query="",
    )


@app.route("/jobs/<int:job_id>")
def job_detail(job_id: int) -> str:
    db = get_db()
    job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    if job is None or (job["is_active"] == 0 and (g.user is None or g.user["role"] != "admin")):
        flash("That job post is not available.", "warning")
        return redirect(url_for("landing"))

    has_applied = False
    if g.user is not None and g.user["role"] == "user":
        applied = db.execute(
            "SELECT id FROM applications WHERE job_id = ? AND user_id = ?",
            (job_id, g.user["id"]),
        ).fetchone()
        has_applied = applied is not None

    return render_template("job_detail.html", job=job, has_applied=has_applied)


@app.route("/jobs/<int:job_id>/apply", methods=["POST"])
@role_required("user")
def apply_for_job(job_id: int) -> str:
    db = get_db()
    job = db.execute(
        "SELECT id, title, description, is_active FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()

    if job is None or job["is_active"] == 0:
        flash("That role is closed for applications.", "warning")
        return redirect(url_for("user_dashboard"))

    file = request.files.get("resume")
    phone = request.form.get("phone", "").strip()

    if file is None or file.filename.strip() == "":
        flash("Please upload your resume PDF before applying.", "danger")
        return redirect(url_for("job_detail", job_id=job_id))

    resume_text = extract_text(file)
    if not resume_text.strip():
        flash("We could not read text from the uploaded resume. Please upload a clear PDF.", "danger")
        return redirect(url_for("job_detail", job_id=job_id))

    skills = extract_skills(resume_text)
    missing = extract_missing_skills(job["description"], resume_text)
    now = utcnow_iso()

    db.execute(
        """
        INSERT INTO applications (
            job_id, user_id, full_name, email, phone, resume_filename, resume_text,
            extracted_skills, score, missing_skills, status, notes, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'submitted', '', ?, ?)
        ON CONFLICT(job_id, user_id)
        DO UPDATE SET
            phone = excluded.phone,
            resume_filename = excluded.resume_filename,
            resume_text = excluded.resume_text,
            extracted_skills = excluded.extracted_skills,
            score = NULL,
            missing_skills = excluded.missing_skills,
            status = 'submitted',
            updated_at = excluded.updated_at
        """,
        (
            job_id,
            g.user["id"],
            g.user["name"],
            g.user["email"],
            phone,
            file.filename,
            resume_text,
            to_json_list(skills),
            to_json_list(missing),
            now,
            now,
        ),
    )
    db.commit()

    flash("Application submitted. The admin can now run AI ranking for this role.", "success")
    return redirect(url_for("user_dashboard"))


@app.route("/admin")
@role_required("admin")
def admin_index() -> str:
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/dashboard")
@role_required("admin")
def admin_dashboard() -> str:
    db = get_db()

    stats = db.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM jobs) AS total_jobs,
            (SELECT COUNT(*) FROM jobs WHERE is_active = 1) AS active_jobs,
            (SELECT COUNT(*) FROM applications) AS total_applications,
            (SELECT COUNT(*) FROM applications WHERE status = 'shortlisted') AS shortlisted_count
        """
    ).fetchone()

    jobs = db.execute(
        """
        SELECT j.id, j.title, j.department, j.location, j.employment_type,
               j.is_active, j.created_at,
               COUNT(a.id) AS applicants,
               AVG(a.score) AS average_score
        FROM jobs j
        LEFT JOIN applications a ON a.job_id = j.id
        GROUP BY j.id
        ORDER BY j.created_at DESC
        """
    ).fetchall()

    recent_rows = db.execute(
        """
        SELECT a.id, a.status, a.score, a.created_at,
               a.extracted_skills, a.missing_skills,
               u.name AS candidate_name,
               j.title AS job_title,
               j.id AS job_id
        FROM applications a
        JOIN users u ON u.id = a.user_id
        JOIN jobs j ON j.id = a.job_id
        ORDER BY a.created_at DESC
        LIMIT 8
        """
    ).fetchall()

    recent_applications = []
    for row in recent_rows:
        entry = dict(row)
        entry["skills"] = from_json_list(entry.pop("extracted_skills", "[]"))
        entry["missing"] = from_json_list(entry.pop("missing_skills", "[]"))
        recent_applications.append(entry)

    score_segments = db.execute(
        """
        SELECT
            SUM(CASE WHEN score IS NULL THEN 1 ELSE 0 END) AS unscored,
            SUM(CASE WHEN score >= 0.75 THEN 1 ELSE 0 END) AS strong_fit,
            SUM(CASE WHEN score >= 0.5 AND score < 0.75 THEN 1 ELSE 0 END) AS moderate_fit,
            SUM(CASE WHEN score < 0.5 THEN 1 ELSE 0 END) AS weak_fit
        FROM applications
        """
    ).fetchone()

    return render_template(
        "admin_dashboard.html",
        stats=stats,
        jobs=jobs,
        recent_applications=recent_applications,
        score_segments=score_segments,
    )


@app.route("/admin/jobs/create", methods=["POST"])
@role_required("admin")
def create_job() -> str:
    title = request.form.get("title", "").strip()
    department = request.form.get("department", "").strip() or "General"
    location = request.form.get("location", "").strip() or "Remote"
    employment_type = request.form.get("employment_type", "").strip() or "Full-time"
    description = request.form.get("description", "").strip()

    if len(title) < 3 or len(description) < 20:
        flash("Please provide a stronger title and a detailed job description.", "danger")
        return redirect(url_for("admin_dashboard"))

    db = get_db()
    db.execute(
        """
        INSERT INTO jobs (title, department, location, employment_type, description, is_active, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (title, department, location, employment_type, description, g.user["id"], utcnow_iso()),
    )
    db.commit()

    flash("New job post published.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/jobs/<int:job_id>/toggle", methods=["POST"])
@role_required("admin")
def toggle_job(job_id: int) -> str:
    db = get_db()
    job = db.execute("SELECT is_active FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        flash("Job not found.", "warning")
        return redirect(url_for("admin_dashboard"))

    db.execute("UPDATE jobs SET is_active = ? WHERE id = ?", (0 if job["is_active"] else 1, job_id))
    db.commit()

    flash("Job visibility updated.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/jobs/<int:job_id>/analyze", methods=["POST"])
@role_required("admin")
def analyze_job(job_id: int) -> str:
    db = get_db()
    job = db.execute("SELECT id, description, title FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        flash("Job not found.", "warning")
        return redirect(url_for("admin_dashboard"))

    applications = db.execute(
        """
        SELECT id, resume_text
        FROM applications
        WHERE job_id = ?
        ORDER BY created_at ASC
        """,
        (job_id,),
    ).fetchall()

    if not applications:
        flash("No applications yet for this role.", "warning")
        return redirect(url_for("admin_job_detail", job_id=job_id))

    resume_texts = [row["resume_text"] for row in applications]
    scores = rank_resumes(resume_texts, job["description"])

    for idx, row in enumerate(applications):
        raw = scores[idx] if idx < len(scores) else 0.0
        score_value = round(extract_score_value(raw), 3)
        resume_text = row["resume_text"]
        skills = extract_skills(resume_text)
        missing = extract_missing_skills(job["description"], resume_text)

        db.execute(
            """
            UPDATE applications
            SET score = ?, extracted_skills = ?, missing_skills = ?, updated_at = ?
            WHERE id = ?
            """,
            (score_value, to_json_list(skills), to_json_list(missing), utcnow_iso(), row["id"]),
        )

    db.commit()

    flash(f"AI scoring refreshed for {len(applications)} application(s) in {job['title']}.", "success")
    return redirect(url_for("admin_job_detail", job_id=job_id))


@app.route("/admin/jobs/<int:job_id>")
@role_required("admin")
def admin_job_detail(job_id: int) -> str:
    db = get_db()
    job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        flash("Job not found.", "warning")
        return redirect(url_for("admin_dashboard"))

    rows = db.execute(
        """
        SELECT a.id, a.full_name, a.email, a.phone, a.resume_filename,
               a.score, a.status, a.notes, a.created_at,
               a.extracted_skills, a.missing_skills
        FROM applications a
        WHERE a.job_id = ?
        ORDER BY (a.score IS NULL) ASC, a.score DESC, a.created_at DESC
        """,
        (job_id,),
    ).fetchall()

    applications = []
    for row in rows:
        record = dict(row)
        record["skills"] = from_json_list(record.pop("extracted_skills", "[]"))
        record["missing"] = from_json_list(record.pop("missing_skills", "[]"))
        applications.append(record)

    return render_template("admin_job.html", job=job, applications=applications)


@app.route("/admin/applications/<int:application_id>/resume")
@role_required("admin")
def admin_application_resume(application_id: int) -> str:
    db = get_db()
    application = db.execute(
        """
        SELECT a.id, a.job_id, a.full_name, a.email, a.phone, a.resume_filename,
               a.resume_text, a.score, a.status, a.notes, a.created_at,
               j.title AS job_title
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        WHERE a.id = ?
        """,
        (application_id,),
    ).fetchone()

    if application is None:
        flash("Application not found.", "warning")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_resume.html", application=application)


@app.route("/admin/applications/<int:application_id>/status", methods=["POST"])
@role_required("admin")
def update_application_status(application_id: int) -> str:
    status = request.form.get("status", "submitted").strip().lower()
    notes = request.form.get("notes", "").strip()
    job_id = request.form.get("job_id", type=int)

    if status not in STATUS_CHOICES:
        flash("Invalid status option.", "danger")
        return redirect(url_for("admin_dashboard"))

    db = get_db()
    application = db.execute(
        """
        SELECT a.id, a.full_name, a.email, j.title AS job_title
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        WHERE a.id = ?
        """,
        (application_id,),
    ).fetchone()

    if application is None:
        flash("Application not found.", "warning")
        return redirect(url_for("admin_dashboard"))

    db.execute(
        "UPDATE applications SET status = ?, notes = ?, updated_at = ? WHERE id = ?",
        (status, notes, utcnow_iso(), application_id),
    )
    db.commit()

    if status in {"shortlisted", "rejected"}:
        sent, message = send_decision_email(
            candidate_email=application["email"],
            candidate_name=application["full_name"],
            job_title=application["job_title"],
            status=status,
            notes=notes,
        )
        if sent:
            flash("Application status updated and email sent.", "success")
        else:
            flash(f"Application status updated, but email failed: {message}", "warning")
    else:
        flash("Application status updated.", "success")

    if job_id:
        return redirect(url_for("admin_job_detail", job_id=job_id))
    return redirect(url_for("admin_dashboard"))


@app.template_filter("percentage")
def percentage_filter(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{float(value) * 100:.1f}%"


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(debug=True)




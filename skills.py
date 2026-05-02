from __future__ import annotations

skills_list = [
    "python",
    "java",
    "javascript",
    "typescript",
    "sql",
    "mysql",
    "postgresql",
    "mongodb",
    "machine learning",
    "deep learning",
    "data science",
    "nlp",
    "flask",
    "django",
    "fastapi",
    "react",
    "node.js",
    "docker",
    "kubernetes",
    "aws",
    "azure",
    "gcp",
    "pandas",
    "numpy",
    "tensorflow",
    "pytorch",
    "power bi",
    "tableau",
    "git",
]


def extract_skills(text: str) -> list[str]:
    source = text.lower()
    found = []

    for skill in skills_list:
        if skill in source:
            found.append(skill)

    return sorted(set(found))


def extract_missing_skills(job_description: str, resume_text: str) -> list[str]:
    jd_skills = set(extract_skills(job_description))
    resume_skills = set(extract_skills(resume_text))
    return sorted(jd_skills - resume_skills)

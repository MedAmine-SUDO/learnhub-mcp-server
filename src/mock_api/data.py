"""Deterministic fake LMS data for two tenants. Every person here is fictional."""

import random
from datetime import date, timedelta

FIRST = ["Amira", "Youssef", "Lea", "Karim", "Sofia", "Omar", "Ines", "Hugo", "Nour", "Adam",
         "Chloe", "Rami", "Emma", "Selim", "Lina", "Lucas", "Maya", "Ziad", "Jade", "Malik"]
LAST = ["Haddad", "Martin", "Benali", "Dubois", "Trabelsi", "Bernard", "Mansour", "Petit",
        "Gharbi", "Moreau", "Saidi", "Laurent", "Jaziri", "Girard", "Khelifi", "Roux"]
DEPARTMENTS = ["Sales", "Engineering", "HR", "Finance", "Operations", "Support", "Marketing"]
CATEGORIES = ["Compliance", "Leadership", "Data", "Security", "Onboarding", "Soft skills", "Product"]
TOPICS = ["Fundamentals", "Advanced Workshop", "Bootcamp", "Certification Prep", "Masterclass", "Essentials"]

# company_id -> (tenant name, email domain, number of users, number of trainings)
TENANTS = {1: ("Acme Corp", "acme.example", 320, 45), 2: ("Globex", "globex.example", 140, 20)}


def _build(company_id: int) -> dict:
    name, domain, n_users, n_trainings = TENANTS[company_id]
    rng = random.Random(company_id)
    base = company_id * 10_000
    start = date(2024, 1, 1)

    users = []
    for i in range(1, n_users + 1):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        users.append({
            "id": base + i,
            "first_name": first,
            "last_name": last,
            "email": f"{first.lower()}.{last.lower()}{i}@{domain}",
            "role": rng.choices(["learner", "instructor", "admin"], weights=[85, 12, 3])[0],
            "status": rng.choices(["active", "inactive"], weights=[90, 10])[0],
            "department": rng.choice(DEPARTMENTS),
            "created_at": (start + timedelta(days=rng.randint(0, 650))).isoformat(),
        })

    instructors = [u["id"] for u in users if u["role"] == "instructor"] or [users[0]["id"]]
    learners = [u["id"] for u in users if u["role"] == "learner"]

    trainings, enrollments = [], []
    for i in range(1, n_trainings + 1):
        category = rng.choice(CATEGORIES)
        training_id = base + 5_000 + i
        trainings.append({
            "id": training_id,
            "title": f"{category} {rng.choice(TOPICS)}",
            "category": category,
            "status": rng.choices(["published", "draft", "archived"], weights=[70, 15, 15])[0],
            "duration_hours": rng.choice([1, 2, 4, 8, 16, 24]),
            "instructor_id": rng.choice(instructors),
            "created_at": (start + timedelta(days=rng.randint(0, 650))).isoformat(),
        })
        for user_id in rng.sample(learners, k=min(len(learners), rng.randint(5, 120))):
            progress = rng.choice([0, 0, 10, 25, 40, 60, 75, 90, 100, 100, 100])
            completion = "completed" if progress == 100 else "not_started" if progress == 0 else "in_progress"
            enrollments.append({
                "training_id": training_id,
                "user_id": user_id,
                "progress_pct": progress,
                "completion": completion,
                "score": rng.randint(55, 100) if completion == "completed" else None,
            })

    return {"name": name, "users": users, "trainings": trainings, "enrollments": enrollments}


DATASETS = {company_id: _build(company_id) for company_id in TENANTS}

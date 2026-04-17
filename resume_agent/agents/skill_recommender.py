"""
Deterministic skill recommendation helpers for onboarding and typeahead.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set


SKILL_CATALOG: Dict[str, Dict[str, object]] = {
    "Python": {"category": "programming_languages", "roles": ["backend", "data", "ml", "automation"]},
    "Java": {"category": "programming_languages", "roles": ["backend", "enterprise"]},
    "JavaScript": {"category": "programming_languages", "roles": ["frontend", "fullstack"]},
    "TypeScript": {"category": "programming_languages", "roles": ["frontend", "fullstack"]},
    "SQL": {"category": "databases", "roles": ["backend", "data", "analytics"]},
    "PostgreSQL": {"category": "databases", "roles": ["backend", "data"]},
    "MySQL": {"category": "databases", "roles": ["backend", "data"]},
    "MongoDB": {"category": "databases", "roles": ["backend", "fullstack"]},
    "Redis": {"category": "databases", "roles": ["backend", "platform"]},
    "AWS": {"category": "cloud_platforms", "roles": ["backend", "platform", "devops", "data"]},
    "Azure": {"category": "cloud_platforms", "roles": ["backend", "platform", "devops", "data"]},
    "GCP": {"category": "cloud_platforms", "roles": ["backend", "platform", "devops", "data"]},
    "Docker": {"category": "tools", "roles": ["backend", "platform", "devops"]},
    "Kubernetes": {"category": "tools", "roles": ["platform", "devops", "backend"]},
    "Terraform": {"category": "tools", "roles": ["platform", "devops"]},
    "Jenkins": {"category": "tools", "roles": ["devops", "platform"]},
    "GitHub Actions": {"category": "tools", "roles": ["devops", "platform", "backend"]},
    "FastAPI": {"category": "frameworks", "roles": ["backend"]},
    "Django": {"category": "frameworks", "roles": ["backend"]},
    "Flask": {"category": "frameworks", "roles": ["backend"]},
    "Spring Boot": {"category": "frameworks", "roles": ["backend", "enterprise"]},
    "Node.js": {"category": "frameworks", "roles": ["backend", "fullstack"]},
    "React": {"category": "frameworks", "roles": ["frontend", "fullstack"]},
    "Vue": {"category": "frameworks", "roles": ["frontend", "fullstack"]},
    "Angular": {"category": "frameworks", "roles": ["frontend", "enterprise"]},
    "Pandas": {"category": "other_technologies", "roles": ["data", "ml"]},
    "NumPy": {"category": "other_technologies", "roles": ["data", "ml"]},
    "Airflow": {"category": "other_technologies", "roles": ["data", "platform"]},
    "Spark": {"category": "other_technologies", "roles": ["data"]},
    "Kafka": {"category": "other_technologies", "roles": ["backend", "platform", "data"]},
    "CI/CD": {"category": "methodologies", "roles": ["backend", "devops", "platform"]},
    "Microservices": {"category": "methodologies", "roles": ["backend", "platform"]},
    "REST APIs": {"category": "methodologies", "roles": ["backend", "fullstack"]},
    "GraphQL": {"category": "methodologies", "roles": ["backend", "frontend", "fullstack"]},
    "Agile": {"category": "methodologies", "roles": ["backend", "frontend", "fullstack", "data"]},
}


ADJACENCY_MAP: Dict[str, List[str]] = {
    "Python": ["FastAPI", "Django", "Flask", "Pandas", "NumPy"],
    "AWS": ["Docker", "Terraform", "Kubernetes", "CI/CD"],
    "Azure": ["Docker", "Terraform", "Kubernetes", "CI/CD"],
    "GCP": ["Docker", "Terraform", "Kubernetes", "CI/CD"],
    "Docker": ["Kubernetes", "CI/CD", "Terraform"],
    "React": ["TypeScript", "GraphQL", "REST APIs"],
    "JavaScript": ["TypeScript", "React", "Node.js"],
    "TypeScript": ["React", "Node.js", "GraphQL"],
    "SQL": ["PostgreSQL", "MySQL"],
    "Kafka": ["Microservices", "AWS", "Docker"],
}


ROLE_KEYWORDS: Dict[str, Set[str]] = {
    "backend": {"backend", "software engineer", "api", "services", "platform engineer"},
    "frontend": {"frontend", "ui", "ux", "web engineer"},
    "fullstack": {"full stack", "fullstack"},
    "data": {"data engineer", "data scientist", "analytics", "machine learning", "ml engineer"},
    "platform": {"platform", "sre", "site reliability", "infrastructure"},
    "devops": {"devops", "build", "release"},
    "enterprise": {"enterprise", "java developer"},
}


def infer_role_tags(job_titles: Sequence[str]) -> Set[str]:
    tags: Set[str] = set()
    for title in job_titles:
        title_lower = (title or "").lower()
        for tag, keywords in ROLE_KEYWORDS.items():
            if any(keyword in title_lower for keyword in keywords):
                tags.add(tag)
    return tags


def build_skill_records(
    categorized_skills: Dict[str, List[str]],
    all_skills: Sequence[str],
    *,
    confidence: float = 0.9,
) -> List[Dict[str, object]]:
    category_by_skill: Dict[str, str] = {}
    for category, skills in categorized_skills.items():
        for skill in skills or []:
            category_by_skill.setdefault(skill, category)

    records: List[Dict[str, object]] = []
    for skill in all_skills:
        records.append(
            {
                "name": skill,
                "category": category_by_skill.get(skill, "general"),
                "confidence": confidence,
                "source": "resume_parse",
            }
        )
    return records


def recommend_profile_skills(
    *,
    detected_skills: Sequence[str],
    confirmed_skills: Sequence[str],
    job_titles: Sequence[str],
    total_years: Optional[float] = None,
) -> List[Dict[str, object]]:
    known = {skill.lower(): skill for skill in [*detected_skills, *confirmed_skills]}
    role_tags = infer_role_tags(job_titles)
    recommendations: List[Dict[str, object]] = []
    seen: Set[str] = set()

    for skill in [*confirmed_skills, *detected_skills]:
        for adjacent in ADJACENCY_MAP.get(skill, []):
            key = adjacent.lower()
            if key in known or key in seen:
                continue
            seen.add(key)
            recommendations.append(
                {
                    "name": adjacent,
                    "category": SKILL_CATALOG.get(adjacent, {}).get("category", "general"),
                    "reason": f"Often paired with confirmed or detected skill '{skill}'",
                    "source": "adjacency_inference",
                    "confidence": 0.55,
                }
            )

    if role_tags:
        for skill, meta in SKILL_CATALOG.items():
            key = skill.lower()
            if key in known or key in seen:
                continue
            skill_roles = set(meta.get("roles", []))
            if role_tags & skill_roles:
                seen.add(key)
                reason = f"Common for target role areas: {', '.join(sorted(role_tags & skill_roles))}"
                if total_years is not None and total_years >= 5:
                    reason += "; suggested for your seniority profile"
                recommendations.append(
                    {
                        "name": skill,
                        "category": meta.get("category", "general"),
                        "reason": reason,
                        "source": "role_inference",
                        "confidence": 0.45,
                    }
                )

    recommendations.sort(key=lambda item: (-float(item["confidence"]), str(item["name"])))
    return recommendations[:20]


def suggest_as_you_type(
    *,
    query: str,
    confirmed_skills: Sequence[str],
    role_tags: Sequence[str] | None = None,
    limit: int = 10,
) -> List[Dict[str, object]]:
    normalized_query = (query or "").strip().lower()
    confirmed = {skill.lower() for skill in confirmed_skills}
    role_tags = set(role_tags or [])
    results: List[Dict[str, object]] = []

    for skill, meta in SKILL_CATALOG.items():
        skill_lower = skill.lower()
        if normalized_query and normalized_query not in skill_lower:
            continue
        if skill_lower in confirmed:
            continue
        score = 1
        if normalized_query and skill_lower.startswith(normalized_query):
            score += 2
        if role_tags and role_tags & set(meta.get("roles", [])):
            score += 1
        results.append(
            {
                "name": skill,
                "category": meta.get("category", "general"),
                "score": score,
            }
        )

    results.sort(key=lambda item: (-int(item["score"]), str(item["name"])))
    return results[:limit]

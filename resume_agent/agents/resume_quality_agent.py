"""
Resume Quality Agent
Single responsibility: Evaluate and improve the quality of the original resume BEFORE tailoring.
This is different from validation (which checks the tailored resume for fabrication).

Based on research from hiring managers and recruiters:
- 75% of resumes are rejected by ATS before human review
- Quantifying achievements increases hireability by 40%
- Recruiters spend only 10 seconds on initial resume review
- 65% of hiring managers hire based on skills alone
- Data-driven accomplishments are 58% more likely to secure interviews
"""

from typing import List, Optional, Tuple, Dict, Any
from dataclasses import dataclass, field
from enum import Enum
import re
import hashlib

from ..services.llm_service import LLMService
from ..utils.logger import logger
from ..utils.metrics import extract_metrics, extract_metrics_from_user_answers, normalize_metric_set
from ..utils.resume_document import ResumeDocument, parse_resume_document


# Research-backed quality criteria weights
QUALITY_WEIGHTS = {
    "quantified_achievements": 15,  # Helpful signal, but not mandatory for every bullet
    "ats_compatibility": 20,  # 75% rejected by ATS
    "action_verbs": 15,  # Strong verbs crucial
    "structure": 15,  # Proper sections
    "relevance": 15,  # Skills-first approach
    "formatting": 10,  # Clean, readable
}


class QualityCategory(Enum):
    """Categories of quality issues (research-backed)"""
    ATS = "ats"  # ATS compatibility issues (75% rejection rate)
    METRICS = "metrics"  # Missing quantification (40% hireability boost)
    ACTION_VERBS = "action_verbs"  # Weak vs strong verbs
    STRUCTURE = "structure"  # Missing sections, organization
    CONTENT = "content"  # Vague descriptions, no context
    FORMATTING = "formatting"  # Readability, visual hierarchy
    KEYWORDS = "keywords"  # Industry/job-specific keywords
    LENGTH = "length"  # Optimal 400-800 words for most roles


class IssueSeverity(Enum):
    """Severity based on research impact"""
    HIGH = "high"  # Directly impacts ATS pass rate or hiring decision
    MEDIUM = "medium"  # Reduces effectiveness significantly
    LOW = "low"  # Nice to have improvements


@dataclass
class QualityIssue:
    """A single quality issue found in the resume"""
    category: QualityCategory
    severity: IssueSeverity
    section: str  # Which section of the resume
    issue: str  # Description of the issue
    suggestion: str  # How to fix it
    id: str = ""
    example: Optional[str] = None  # Example of improved text
    research_note: Optional[str] = None  # Why this matters (research-backed)
    target_text: Optional[str] = None  # Specific line/snippet this issue refers to
    target_entry_id: Optional[str] = None  # Stable structured entry ID when available
    requires_user_input: bool = False  # True when the system needs facts from the user
    blocked_reason: Optional[str] = None  # Why the suggestion cannot be auto-applied
    advisory_only: bool = False  # True when this is a soft recommendation, not a blocker
    score_component: Optional[str] = None  # Visible scoring bucket
    impact_level: Optional[str] = None  # blocking | high_leverage | advisory | optional
    proposed_fix: Optional[str] = None  # Proposed rewrite or tighter suggestion


@dataclass
class ClarifyingQuestion:
    """Question to ask user before improving resume"""
    id: str
    question: str
    context: str  # Why we're asking
    options: Optional[List[str]] = None  # Multiple choice options if applicable
    required: bool = True


@dataclass
class QualityReport:
    """Complete quality analysis of a resume"""
    overall_score: int  # 0-100
    issues: List[QualityIssue] = field(default_factory=list)
    strengths: List[str] = field(default_factory=list)
    improvement_priority: List[str] = field(default_factory=list)
    estimated_impact: str = ""  # How much improvement would help
    ats_score: int = 0  # Separate ATS compatibility score
    metrics_count: int = 0  # Number of quantified achievements
    questions: List[ClarifyingQuestion] = field(default_factory=list)  # Questions before improving
    subscores: List[Dict[str, Any]] = field(default_factory=list)
    top_driver: Optional[Dict[str, Any]] = None
    best_next_fix: Optional[Dict[str, Any]] = None


@dataclass
class ImprovedResume:
    """Result of improving a resume"""
    improved_text: str
    changes_made: List[str]
    before_score: int
    after_score: int
    metrics_added: int = 0  # How many achievements were quantified
    accepted: bool = True
    score_regressed: bool = False
    after_report: Optional[QualityReport] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)


class QualityReviewerAgent:
    """
    Fixed-rubric reviewer for resume quality.

    This is the analysis layer for quality review. It operates on the normalized
    resume plus a structured in-memory document, and returns issue buckets using
    stable review logic rather than ad hoc prompt-only behavior.
    """

    REVIEW_STANDARD = "industry_resume_quality_v1"

    def __init__(self, quality_agent: "ResumeQualityAgent"):
        self.quality_agent = quality_agent

    def review(self, resume_text: str, resume_document: ResumeDocument) -> Dict[str, Any]:
        ats_issues, ats_score = self.quality_agent._analyze_ats_compatibility(resume_text)
        metrics_issues, metrics_count = self.quality_agent._analyze_metrics(resume_text, resume_document)
        verb_issues = self.quality_agent._analyze_action_verbs(resume_text)
        structure_issues = self.quality_agent._analyze_structure(resume_text)
        content_issues = self.quality_agent._analyze_content(resume_text, resume_document)

        issues = ats_issues + metrics_issues + verb_issues + structure_issues + content_issues
        self.quality_agent._enrich_issues_with_context(resume_text, issues, resume_document)

        return {
            "issues": issues,
            "ats_score": ats_score,
            "metrics_count": metrics_count,
            "standard": self.REVIEW_STANDARD,
            "document_section_count": len(resume_document.sections),
            "document_entry_count": sum(1 for _ in resume_document.iter_entries()),
        }


class ResumeQualityAgent:
    """
    Agent responsible for evaluating and improving resume quality.
    
    Based on research findings:
    - 75% of resumes rejected by ATS before human review
    - Quantifying achievements increases hireability by 40%
    - Recruiters spend only 10 seconds on initial review
    - 65% of hiring managers hire based on skills alone
    - Data-driven accomplishments 58% more likely to secure interviews
    
    This agent:
    - Checks ATS compatibility (formatting, keywords)
    - Finds unquantified achievements (40% hireability boost when fixed)
    - Evaluates action verbs strength
    - Analyzes structure and content quality
    - Generates clarifying questions before improving
    """
    
    # Research-backed weak vs strong action verbs
    WEAK_VERBS = [
        'helped', 'assisted', 'worked on', 'was responsible for',
        'participated', 'contributed', 'supported', 'handled',
        'involved in', 'dealt with', 'managed'
    ]
    
    STRONG_VERBS = [
        'led', 'developed', 'implemented', 'designed', 'created',
        'built', 'launched', 'achieved', 'increased', 'reduced',
        'improved', 'optimized', 'automated', 'streamlined', 'delivered',
        'spearheaded', 'pioneered', 'transformed', 'orchestrated', 'architected'
    ]
    
    # ATS-problematic elements
    ATS_ISSUES = [
        ('tables', r'<table|│|├|└|┬|┴'),
        ('images', r'<img|\.png|\.jpg|\.jpeg'),
        ('graphics', r'<svg|<canvas'),
        ('multiple_columns', r'^\s{20,}.*\s{20,}'),
        ('special_characters', r'[→←↑↓★☆●○■□▪▫]'),
    ]

    SCORE_COMPONENTS = {
        "discoverability": {
            "label": "ATS Discoverability",
            "weight": 0.30,
            "categories": {QualityCategory.ATS, QualityCategory.STRUCTURE},
        },
        "clarity": {
            "label": "Clarity",
            "weight": 0.25,
            "categories": {QualityCategory.CONTENT},
        },
        "evidence": {
            "label": "Evidence",
            "weight": 0.20,
            "categories": {QualityCategory.METRICS},
        },
        "language": {
            "label": "Language",
            "weight": 0.15,
            "categories": {QualityCategory.ACTION_VERBS},
        },
        "formatting": {
            "label": "Formatting",
            "weight": 0.10,
            "categories": {QualityCategory.FORMATTING, QualityCategory.LENGTH},
        },
    }
    
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
        self.reviewer = QualityReviewerAgent(self)
    
    def analyze_quality(self, resume_text: str) -> QualityReport:
        """
        Analyze resume quality using research-backed criteria.
        
        Research basis:
        - ATS rejection rate: 75% (check formatting, keywords)
        - Quantified achievements: +40% hireability
        - Strong action verbs: Crucial for 10-second scan
        - Skills-first: 65% of hiring managers prioritize
        """
        logger.info("Resume Quality Agent: Starting research-backed quality analysis")
        resume_text = self._normalize_resume_layout(resume_text)
        resume_document = parse_resume_document(resume_text)
        review = self.reviewer.review(resume_text, resume_document)
        all_issues = review["issues"]
        ats_score = review["ats_score"]
        metrics_count = review["metrics_count"]
        subscores = self._build_subscores(all_issues)
        
        # Calculate weighted score based on research
        overall_score = self._calculate_visible_overall_score(subscores, all_issues)
        
        # Identify strengths
        strengths = self._identify_strengths(resume_text)
        
        # Prioritize based on research impact
        priority = self._prioritize_by_impact(all_issues)
        
        # Generate clarifying questions for improvement
        questions = self._generate_clarifying_questions(resume_text, all_issues)
        
        # Estimate impact with research data
        impact = self._estimate_research_backed_impact(overall_score, metrics_count, ats_score)
        top_driver = self._select_top_driver(subscores)
        best_next_fix = self._select_best_next_fix(all_issues, top_driver)
        
        report = QualityReport(
            overall_score=overall_score,
            issues=all_issues,
            strengths=strengths,
            improvement_priority=priority,
            estimated_impact=impact,
            ats_score=ats_score,
            metrics_count=metrics_count,
            questions=questions,
            subscores=subscores,
            top_driver=top_driver,
            best_next_fix=best_next_fix,
        )
        
        logger.info(
            "Resume Quality Agent: Analysis complete",
            score=overall_score,
            ats_score=ats_score,
            metrics_count=metrics_count,
            issues_found=len(all_issues),
            review_standard=review["standard"],
            document_section_count=review["document_section_count"],
            document_entry_count=review["document_entry_count"],
        )
        
        return report

    def _normalize_resume_layout(self, resume_text: str) -> str:
        """Restore basic line boundaries so analysis does not treat the whole resume as one sentence."""
        text = (resume_text or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"(\*\*[^*]{1,120}\*\*)(?=\*\*)", r"\1\n", text)
        # Section/job headers often come back glued together after an LLM rewrite.
        text = re.sub(r"(?<!\n)(\*\*[A-Z][^*]{1,120}\*\*)", r"\n\1", text)
        text = re.sub(r"(\*\*[^*]{1,120}\*\*)(?=[^\n])", r"\1\n", text)
        text = re.sub(r"\*\*\n([^*\n][^*]{1,120})\*\*", r"**\1**", text)
        # Keep bullets on their own lines.
        text = re.sub(r"\s+(?=[•*-]\s)", "\n", text)
        # Separate year/date ranges that run into the next bold header.
        text = re.sub(r"(\d{4})(\*\*[A-Z])", r"\1\n\2", text)
        # Collapse runaway whitespace without removing deliberate newlines.
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _enrich_issues_with_context(
        self,
        resume_text: str,
        issues: List[QualityIssue],
        resume_document: Optional[ResumeDocument] = None,
    ) -> None:
        """Assign stable IDs and attach the most relevant line/snippet for each issue."""
        lines = [line.strip() for line in resume_text.splitlines() if line.strip()]
        long_sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", resume_text) if len(sentence.split()) > 35]
        long_lines = [line for line in lines if len(line.split()) > 35]
        # Bullets should look like "- something", "• something", "* something".
        # Do not treat markdown headers like "**CONTACT**" as bullets.
        bullet_candidates = [
            line for line in lines
            if re.match(r"^(-|•|\*)\s+", line) and not re.search(r"\d|%|\$", line)
        ]

        for idx, issue in enumerate(issues):
            if not issue.id:
                stable = "|".join(
                    [
                        issue.category.value,
                        issue.severity.value,
                        (issue.section or "").strip().lower(),
                        (issue.issue or "").strip().lower(),
                        (issue.suggestion or "").strip().lower(),
                    ]
                )
                digest = hashlib.sha1(stable.encode("utf-8")).hexdigest()[:10]
                section_slug = re.sub(r"[^a-z0-9]+", "-", (issue.section or "general").lower()).strip("-")
                issue.id = f"{issue.category.value}-{section_slug}-{digest}"
            issue.proposed_fix = issue.proposed_fix or issue.example or issue.suggestion
            issue.score_component = issue.score_component or self._score_component_for_issue(issue)
            issue.impact_level = issue.impact_level or self._impact_level_for_issue(issue)

            if issue.target_text and issue.target_entry_id:
                continue

            extracted = self._extract_issue_target_text(
                issue,
                lines,
                long_sentences,
                bullet_candidates,
                long_lines,
                resume_document,
            )
            if extracted:
                issue.target_text = extracted
                if resume_document:
                    entry = resume_document.find_entry_by_text(extracted)
                    if entry:
                        issue.target_entry_id = entry.id

    def _extract_issue_target_text(
        self,
        issue: QualityIssue,
        lines: List[str],
        long_sentences: List[str],
        bullet_candidates: List[str],
        long_lines: List[str],
        resume_document: Optional[ResumeDocument],
    ) -> Optional[str]:
        issue_text_lower = issue.issue.lower()

        quoted_match = re.search(r"'([^']+)'", issue.issue)
        if quoted_match:
            needle = quoted_match.group(1).strip().lower()
            for line in lines:
                if needle in line.lower():
                    return line

        if "overly long sentence" in issue_text_lower:
            if resume_document:
                long_entries = [
                    entry.text
                    for entry in resume_document.iter_entries()
                    if len(entry.text.split()) > 35
                ]
                if long_entries:
                    return long_entries[0]
            if long_lines:
                return long_lines[0]
            if long_sentences:
                return long_sentences[0]

        if issue.category == QualityCategory.METRICS:
            if resume_document:
                for entry in resume_document.iter_entries():
                    if entry.kind == "bullet" and not re.search(r"\d|%|\$", entry.text):
                        return entry.text
            # Prefer a non-quantified experience bullet if we can find an Experience section.
            exp_start = None
            for i, line in enumerate(lines):
                if re.search(r"\b(experience|work experience|professional experience)\b", line.lower()):
                    exp_start = i
                    break
            if exp_start is not None:
                for line in lines[exp_start : min(len(lines), exp_start + 120)]:
                    if re.match(r"^(-|•|\*)\s+", line) and not re.search(r"\d|%|\$", line):
                        return line
            if bullet_candidates:
                return bullet_candidates[0]

        if issue.category == QualityCategory.ACTION_VERBS:
            weak_verbs = [verb for verb in self.WEAK_VERBS if verb in issue_text_lower]
            for weak_verb in weak_verbs:
                for line in lines:
                    if weak_verb in line.lower():
                        return line

        passive_match = re.search(r"'([^']+)'", issue.issue)
        if passive_match:
            token = passive_match.group(1).lower()
            for line in lines:
                if token in line.lower():
                    return line

        return None

    def _score_component_for_issue(self, issue: QualityIssue) -> str:
        for component_id, config in self.SCORE_COMPONENTS.items():
            if issue.category in config["categories"]:
                return component_id
        return "clarity"

    def _impact_level_for_issue(self, issue: QualityIssue) -> str:
        if issue.advisory_only:
            return "advisory"
        if issue.requires_user_input:
            return "high_leverage"
        if issue.severity == IssueSeverity.HIGH:
            return "blocking"
        if issue.severity == IssueSeverity.MEDIUM:
            return "high_leverage"
        return "optional"

    def _issue_penalty(self, issue: QualityIssue) -> int:
        if issue.advisory_only:
            return 4
        if issue.severity == IssueSeverity.HIGH:
            return 24
        if issue.severity == IssueSeverity.MEDIUM:
            return 14
        return 8

    def _build_subscores(self, issues: List[QualityIssue]) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[QualityIssue]] = {component_id: [] for component_id in self.SCORE_COMPONENTS}
        for issue in issues:
            grouped[self._score_component_for_issue(issue)].append(issue)

        subscores: List[Dict[str, Any]] = []
        for component_id, config in self.SCORE_COMPONENTS.items():
            component_issues = grouped.get(component_id, [])
            score = max(0, 100 - sum(self._issue_penalty(issue) for issue in component_issues))
            weakest_reason = component_issues[0].issue if component_issues else "No major issues surfaced"
            subscores.append(
                {
                    "id": component_id,
                    "label": config["label"],
                    "score": score,
                    "weight": config["weight"],
                    "issue_count": len(component_issues),
                    "weakest_reason": weakest_reason,
                }
            )
        return subscores

    def _calculate_visible_overall_score(self, subscores: List[Dict[str, Any]], issues: List[QualityIssue]) -> int:
        weighted = sum(item["score"] * item["weight"] for item in subscores)
        issue_drag = min(28, len(issues) * 7)
        overall = int(round(weighted - issue_drag))
        return max(0, min(100, overall))

    def _select_top_driver(self, subscores: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not subscores:
            return None
        weakest = min(subscores, key=lambda item: (item["score"], -item["issue_count"]))
        return {
            "component_id": weakest["id"],
            "label": weakest["label"],
            "score": weakest["score"],
            "reason": weakest["weakest_reason"],
        }

    def _select_best_next_fix(
        self,
        issues: List[QualityIssue],
        top_driver: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not issues:
            return None

        ranked = sorted(
            issues,
            key=lambda issue: (
                0 if top_driver and issue.score_component == top_driver["component_id"] else 1,
                0 if issue.severity == IssueSeverity.HIGH else 1 if issue.severity == IssueSeverity.MEDIUM else 2,
                1 if issue.advisory_only else 0,
            ),
        )
        issue = ranked[0]
        expected_impact = "small" if issue.advisory_only or issue.severity == IssueSeverity.LOW else "moderate" if issue.severity == IssueSeverity.MEDIUM else "high"
        return {
            "issue_id": issue.id,
            "component_id": issue.score_component,
            "label": issue.issue,
            "suggestion": issue.suggestion,
            "target_text": issue.target_text,
            "target_entry_id": issue.target_entry_id,
            "impact_level": issue.impact_level,
            "expected_impact": expected_impact,
        }
    
    def _analyze_ats_compatibility(self, resume_text: str) -> Tuple[List[QualityIssue], int]:
        """
        Check ATS compatibility (75% of resumes rejected by ATS).
        
        Research: Clean single-column layout, standard fonts, no tables/graphics.
        """
        import re
        issues = []
        score = 100
        
        # Check for ATS-problematic elements
        for issue_name, pattern in self.ATS_ISSUES:
            if re.search(pattern, resume_text, re.IGNORECASE | re.MULTILINE):
                issues.append(QualityIssue(
                    category=QualityCategory.ATS,
                    severity=IssueSeverity.HIGH,
                    section="Formatting",
                    issue=f"ATS may have trouble parsing: {issue_name.replace('_', ' ')}",
                    suggestion=f"Remove {issue_name.replace('_', ' ')} for better ATS compatibility",
                    research_note="75% of resumes are rejected by ATS before human review"
                ))
                score -= 15
        
        # Check for standard sections
        required_sections = ['experience', 'education', 'skills']
        resume_lower = resume_text.lower()
        for section in required_sections:
            if section not in resume_lower:
                issues.append(QualityIssue(
                    category=QualityCategory.ATS,
                    severity=IssueSeverity.HIGH,
                    section="Structure",
                    issue=f"Missing standard section: {section.title()}",
                    suggestion=f"Add a clear '{section.title()}' section header",
                    research_note="ATS looks for standard section headers to parse content"
                ))
                score -= 10
        
        # Check for contact info
        has_email = bool(re.search(r'[\w\.-]+@[\w\.-]+\.\w+', resume_text))
        has_phone = bool(re.search(r'[\d\-\(\)\s]{10,}', resume_text))
        
        if not has_email or not has_phone:
            issues.append(QualityIssue(
                category=QualityCategory.ATS,
                severity=IssueSeverity.HIGH,
                section="Header",
                issue="Missing contact information (email/phone)",
                suggestion="Add your email address and phone number at the top",
                research_note="Recruiters need contact info to reach you"
            ))
            score -= 20
        
        return issues, max(0, score)
    
    def _analyze_metrics(
        self,
        resume_text: str,
        resume_document: Optional[ResumeDocument] = None,
    ) -> Tuple[List[QualityIssue], int]:
        """
        Check for quantified achievements (40% increase in hireability).
        
        Research: Numbers make claims credible and memorable.
        """
        import re
        issues = []
        
        # Count metrics (numbers, percentages, dollar amounts)
        metrics_patterns = [
            r'\d+%',  # Percentages
            r'\$[\d,]+',  # Dollar amounts
            r'\d+x',  # Multipliers
            r'\d+\+?\s*(users|customers|clients|employees|team members)',  # Scale
            r'(increased|decreased|improved|reduced|grew|saved).*?\d+',  # Impact with numbers
        ]
        
        metrics_count = 0
        for pattern in metrics_patterns:
            metrics_count += len(re.findall(pattern, resume_text, re.IGNORECASE))
        
        # Check for unquantified achievements
        vague_achievement_patterns = [
            r'significantly (improved|increased|reduced|decreased)',
            r'greatly (improved|increased|reduced|decreased)',
            r'successfully (led|managed|completed)',
            r'improved (performance|efficiency|productivity)',
            r'responsible for (managing|leading|developing)',
        ]
        
        for pattern in vague_achievement_patterns:
            matches = re.findall(pattern, resume_text, re.IGNORECASE)
            if matches:
                issues.append(QualityIssue(
                    category=QualityCategory.METRICS,
                    severity=IssueSeverity.HIGH,
                    section="Experience",
                    issue=f"Vague achievement without metrics: '{matches[0]}'",
                    suggestion="Quantify with specific numbers: 'Improved X by Y%, resulting in Z'",
                    example="Instead of 'Significantly improved performance', write 'Improved response time by 40%, reducing user wait time from 3s to 1.8s'",
                    research_note="Quantifying achievements increases hireability by 40%"
                ))
        
        # Warn if few metrics overall
        word_count = len(resume_text.split())
        expected_metrics = max(3, word_count // 150)  # Expect ~1 metric per 150 words
        
        if metrics_count < expected_metrics:
            target_text = None
            target_entry_id = None
            if resume_document:
                for entry in resume_document.iter_entries():
                    if entry.kind == "bullet" and not re.search(r"\d|%|\$", entry.text):
                        target_text = entry.text
                        target_entry_id = entry.id
                        break
            issues.append(QualityIssue(
                category=QualityCategory.METRICS,
                severity=IssueSeverity.LOW,
                section="General",
                issue=f"Only {metrics_count} quantified achievements found (expected {expected_metrics}+)",
                suggestion="If you have them, add numbers. If not, make the outcome more concrete in words.",
                research_note="Data-driven accomplishments are 58% more likely to secure interviews",
                target_text=target_text,
                target_entry_id=target_entry_id,
                requires_user_input=False,
                blocked_reason=None,
                advisory_only=True,
            ))
        
        return issues, metrics_count
    
    def _analyze_action_verbs(self, resume_text: str) -> List[QualityIssue]:
        """
        Check for strong vs weak action verbs.
        
        Research: Recruiters scan in 10 seconds - strong verbs catch attention.
        """
        issues = []
        resume_lower = resume_text.lower()
        
        # Find weak verbs
        weak_verbs_found = []
        for verb in self.WEAK_VERBS:
            if verb in resume_lower:
                weak_verbs_found.append(verb)
        
        if weak_verbs_found:
            issues.append(QualityIssue(
                category=QualityCategory.ACTION_VERBS,
                severity=IssueSeverity.MEDIUM,
                section="Experience",
                issue=f"Weak action verbs detected: {', '.join(weak_verbs_found[:5])}",
                suggestion=f"Replace with stronger verbs like: {', '.join(self.STRONG_VERBS[:5])}",
                example="Instead of 'Helped with deployment', write 'Led deployment of 15 microservices'",
                research_note="Recruiters spend only 10 seconds on initial review - strong verbs stand out"
            ))
        
        return issues
    
    def _generate_clarifying_questions(
        self, resume_text: str, issues: List[QualityIssue]
    ) -> List[ClarifyingQuestion]:
        """
        Generate SPECIFIC questions based on resume content.
        Questions are role-specific and actionable, not generic.
        """
        import re
        questions = []
        
        # Extract company/role names for specific questions
        companies = self._extract_companies(resume_text)
        
        # Check for metrics issues - ask about specific achievements
        metrics_issues = [i for i in issues if i.category == QualityCategory.METRICS]
        if metrics_issues:
            # Create ONE comprehensive question with examples
            example_format = """Format: "Role @ Company: metric1, metric2"
Example answers:
- "Senior Dev @ Acme: Led team of 8, reduced deploy time by 40%, handled 1M+ daily requests"
- "QA Lead @ TechCorp: Automated 200+ test cases, reduced bugs by 60%, managed 5-person team"
"""
            if companies:
                company_list = ", ".join(companies[:3])
                question_text = f"Add metrics for your roles (we found: {company_list})"
            else:
                question_text = "Add metrics for each of your roles"
            
            questions.append(ClarifyingQuestion(
                id="metrics_by_role",
                question=question_text,
                context=f"Quantified achievements increase hireability by 40%.\n\n{example_format}",
                options=None,
                required=False
            ))
        
        # Check for missing summary - ask for target role
        if 'summary' not in resume_text.lower() and 'objective' not in resume_text.lower():
            questions.append(ClarifyingQuestion(
                id="career_focus",
                question="What type of role are you targeting?",
                context="This helps us write a compelling professional summary. Example: 'Senior Backend Engineer focused on distributed systems'",
                options=None,
                required=True
            ))
        
        # Ask about certifications only if none found
        if 'certification' not in resume_text.lower() and 'certified' not in resume_text.lower():
            questions.append(ClarifyingQuestion(
                id="certifications",
                question="List any certifications you have (or type 'none')",
                context="Examples: AWS Solutions Architect, Google Cloud Professional, PMP, Scrum Master, etc.",
                options=None,
                required=False
            ))
        
        # Ask about notable achievements not on resume
        questions.append(ClarifyingQuestion(
            id="notable_achievements",
            question="Any notable achievements not fully captured in your resume?",
            context="Examples: 'Promoted twice in 2 years', 'Patent holder', 'Reduced costs by $500K', 'Grew team from 3 to 15'",
            options=None,
            required=False
        ))
        
        return questions
    
    def _extract_companies(self, resume_text: str) -> List[str]:
        """Extract company names from resume for specific questions."""
        import re
        companies = []
        
        # Common patterns for company names in resumes
        patterns = [
            r'(?:at|@)\s+([A-Z][A-Za-z0-9\s&]+?)(?:\s*[-–|,]|\s+\d{4})',
            r'([A-Z][A-Za-z0-9\s&]+?)\s*[-–|]\s*(?:Senior|Junior|Lead|Staff|Principal|Software|Engineer|Developer|Manager)',
            r'(?:Company|Employer):\s*([A-Z][A-Za-z0-9\s&]+)',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, resume_text)
            for match in matches:
                company = match.strip()
                if len(company) > 2 and len(company) < 50 and company not in companies:
                    companies.append(company)
        
        return companies[:5]  # Limit to 5 most recent
    
    def _calculate_research_based_score(
        self, ats_score: int, metrics_count: int, issues: List[QualityIssue], resume_text: str
    ) -> int:
        """
        Calculate score using research-backed weights.
        
        Weights based on research:
        - ATS compatibility: 20% (75% rejection rate)
        - Quantified achievements: 25% (40% hireability increase)
        - Action verbs: 15%
        - Structure: 15%
        - Relevance/Content: 15%
        - Formatting: 10%
        """
        # Base score from ATS compatibility (20% weight)
        score = ats_score * 0.20
        
        # Metrics score (25% weight) - based on count
        word_count = len(resume_text.split())
        expected_metrics = max(3, word_count // 150)
        metrics_ratio = min(1.0, metrics_count / expected_metrics)
        score += metrics_ratio * 25
        
        # Deduct for issues by category
        category_deductions = {
            QualityCategory.ATS: 5,
            QualityCategory.METRICS: 4,
            QualityCategory.ACTION_VERBS: 3,
            QualityCategory.STRUCTURE: 4,
            QualityCategory.CONTENT: 3,
            QualityCategory.FORMATTING: 2,
        }
        
        for issue in issues:
            severity_multiplier = 1.5 if issue.severity == IssueSeverity.HIGH else (1.0 if issue.severity == IssueSeverity.MEDIUM else 0.5)
            deduction = category_deductions.get(issue.category, 2) * severity_multiplier
            score -= deduction
        
        # Base points for having content
        score += 50  # Base 50 points for having a resume
        
        return max(0, min(100, int(score)))
    
    def _prioritize_by_impact(self, issues: List[QualityIssue]) -> List[str]:
        """
        Prioritize improvements based on research impact.
        Only show priorities for HIGH/MEDIUM severity issues with specific details.
        """
        priorities = []
        
        # Only consider HIGH and MEDIUM severity issues
        significant_issues = [i for i in issues if i.severity in [IssueSeverity.HIGH, IssueSeverity.MEDIUM]]
        
        if not significant_issues:
            return ["✅ No major issues found - your resume looks good!"]
        
        # ATS first (highest impact) - only if HIGH severity
        ats_high = [i for i in significant_issues if i.category == QualityCategory.ATS and i.severity == IssueSeverity.HIGH]
        if ats_high:
            specific = ats_high[0].issue[:60] if ats_high else ""
            priorities.append(f"🚨 ATS Issue: {specific}")
        
        # Metrics second - be specific
        metrics_issues = [i for i in significant_issues if i.category == QualityCategory.METRICS]
        if metrics_issues:
            count = len(metrics_issues)
            priorities.append(f"📊 Add metrics to {count} achievement(s) - increases hireability by 40%")
        
        # Action verbs - only if found
        verb_issues = [i for i in significant_issues if i.category == QualityCategory.ACTION_VERBS]
        if verb_issues:
            priorities.append(f"💪 Replace {len(verb_issues)} weak verb(s) with stronger alternatives")
        
        # Structure - be specific
        structure_issues = [i for i in significant_issues if i.category == QualityCategory.STRUCTURE]
        if structure_issues:
            specific = structure_issues[0].issue[:50] if structure_issues else ""
            priorities.append(f"📋 Structure: {specific}")
        
        # Content - be specific
        content_issues = [i for i in significant_issues if i.category == QualityCategory.CONTENT]
        if content_issues:
            specific = content_issues[0].issue[:50] if content_issues else ""
            priorities.append(f"✍️ Content: {specific}")
        
        return priorities[:5] if priorities else ["✅ Looking good! Only minor suggestions remain."]
    
    def _estimate_research_backed_impact(self, score: int, metrics_count: int, ats_score: int) -> str:
        """
        Estimate impact using research data.
        """
        # High score - good job!
        if score >= 85:
            return "✅ Excellent! Your resume is well-optimized. Ready for tailoring to specific jobs."
        
        if score >= 75:
            minor_tips = []
            if metrics_count < 5:
                minor_tips.append("adding a few more metrics")
            if ats_score < 90:
                minor_tips.append("minor ATS tweaks")
            tip_text = " and ".join(minor_tips) if minor_tips else "polish"
            return f"✅ Strong resume! Consider {tip_text} for maximum impact."
        
        impacts = []
        
        if ats_score < 70:
            impacts.append("Fixing ATS issues could increase your interview callback rate significantly")
        
        if metrics_count < 3:
            impacts.append("Adding quantified achievements could increase your hireability by up to 40%")
        
        if score >= 60:
            return f"⚠️ Good foundation with room for improvement. {' '.join(impacts)}"
        else:
            return f"🔴 Significant improvements needed. {' '.join(impacts)} Addressing these could dramatically increase your interview rate."
    
    def improve_resume(
        self,
        resume_text: str,
        quality_report: Optional[QualityReport] = None,
        fix_high_only: bool = False,
        user_answers: Optional[Dict[str, Any]] = None,
        issue_resolutions: Optional[Dict[str, Any]] = None,
    ) -> ImprovedResume:
        """
        Improve the resume based on quality analysis and user answers.
        
        Args:
            resume_text: Original resume text
            quality_report: Optional pre-computed quality report
            fix_high_only: If True, only fix HIGH severity issues
            user_answers: User's answers to clarifying questions
            issue_resolutions: Per-issue approve/skip/custom instructions from the UI
            
        Returns:
            ImprovedResume with the enhanced text
        """
        logger.info("Resume Quality Agent: Starting improvement with user context")
        
        # Get quality report if not provided
        if quality_report is None:
            quality_report = self.analyze_quality(resume_text)
        
        before_score = quality_report.overall_score
        effective_user_answers = self._merge_issue_resolution_inputs(
            user_answers or {},
            quality_report.issues,
            issue_resolutions or {},
        )

        # Filter issues to fix
        issues_to_fix = quality_report.issues
        if fix_high_only:
            issues_to_fix = [i for i in issues_to_fix if i.severity == IssueSeverity.HIGH]

        has_metrics_answers = bool(
            effective_user_answers
            and any(
                effective_user_answers.get(k)
                for k in ("metrics_by_role", "metrics_details", "team_size", "project_scale", "notable_achievements")
            )
        )
        blocked_metric_issue_ids = [
            i.id for i in issues_to_fix
            if i.category == QualityCategory.METRICS and i.requires_user_input and not has_metrics_answers
        ]
        if blocked_metric_issue_ids:
            issues_to_fix = [
                i for i in issues_to_fix
                if not (i.category == QualityCategory.METRICS and i.requires_user_input and not has_metrics_answers)
            ]

        requested_issue_ids = [issue.id for issue in issues_to_fix]
        issues_to_fix, resolution_guidance = self._apply_issue_resolutions(
            issues_to_fix,
            issue_resolutions or {},
        )
        active_issue_ids = [issue.id for issue in issues_to_fix]
        skipped_issue_ids = [issue_id for issue_id in requested_issue_ids if issue_id not in active_issue_ids]
        diagnostics: Dict[str, Any] = {
            "requested_issue_ids": requested_issue_ids,
            "active_issue_ids": active_issue_ids,
            "skipped_issue_ids": skipped_issue_ids,
            "has_user_answers": bool(user_answers),
            "has_effective_user_answers": bool(effective_user_answers),
            "has_metrics_answers": has_metrics_answers,
            "resolution_guidance_count": len(resolution_guidance),
            "blocked_metric_issue_ids": blocked_metric_issue_ids,
        }

        if not issues_to_fix:
            logger.info("Resume Quality Agent: No issues to fix")
            return ImprovedResume(
                improved_text=resume_text,
                changes_made=[],
                before_score=before_score,
                after_score=before_score,
                accepted=True,
                score_regressed=False,
                after_report=quality_report,
                diagnostics={**diagnostics, "reason": "no_active_issues"},
            )
        
        # Improve the resume with user context
        improved_text, changes, metrics_added, improvement_debug = self._apply_improvements_with_context(
            resume_text, issues_to_fix, effective_user_answers, resolution_guidance
        )
        diagnostics.update(improvement_debug)
        diagnostics["text_changed"] = improved_text != resume_text
        diagnostics["character_delta"] = len(improved_text) - len(resume_text)
        
        # Re-analyze to get new score (for display only - we always return the improved text and let the user decide)
        new_report = self.analyze_quality(improved_text)
        after_score = new_report.overall_score
        diagnostics["before_issue_count"] = len(quality_report.issues)
        diagnostics["after_issue_count"] = len(new_report.issues)
        diagnostics["before_score"] = before_score
        diagnostics["after_score"] = after_score

        if after_score < before_score:
            logger.info(
                "Improved version scored lower; returning candidate draft with warning",
                before_score=before_score,
                after_score=after_score,
                diagnostics=diagnostics,
            )
            return ImprovedResume(
                improved_text=improved_text,
                changes_made=changes,
                before_score=before_score,
                after_score=after_score,
                metrics_added=metrics_added,
                accepted=False,
                score_regressed=True,
                after_report=new_report,
                diagnostics=diagnostics,
            )

        logger.info(
            "Resume Quality Agent: Improvement complete",
            before_score=before_score,
            after_score=after_score,
            changes_made=len(changes),
            metrics_added=metrics_added,
            diagnostics=diagnostics,
        )

        return ImprovedResume(
            improved_text=improved_text,
            changes_made=changes,
            before_score=before_score,
            after_score=after_score,
            metrics_added=metrics_added,
            accepted=True,
            score_regressed=False,
            after_report=new_report,
            diagnostics=diagnostics,
        )
    
    def _analyze_structure(self, resume_text: str) -> List[QualityIssue]:
        """
        Analyze resume structure using RULE-BASED checks (no LLM = consistent results).
        """
        import re
        issues = []
        resume_lower = resume_text.lower()
        
        # Check for essential sections
        essential_sections = {
            'summary': ['summary', 'professional summary', 'objective', 'profile'],
            'experience': ['experience', 'work experience', 'employment', 'work history'],
            'education': ['education', 'academic', 'qualification'],
            'skills': ['skills', 'technical skills', 'technologies', 'competencies']
        }
        
        for section_name, keywords in essential_sections.items():
            found = any(kw in resume_lower for kw in keywords)
            if not found:
                issues.append(QualityIssue(
                    category=QualityCategory.STRUCTURE,
                    severity=IssueSeverity.MEDIUM if section_name == 'summary' else IssueSeverity.HIGH,
                    section="Structure",
                    issue=f"Missing '{section_name.title()}' section",
                    suggestion=f"Add a clear '{section_name.title()}' section header"
                ))
        
        # Check word count
        word_count = len(resume_text.split())
        if word_count < 200:
            issues.append(QualityIssue(
                category=QualityCategory.STRUCTURE,
                severity=IssueSeverity.HIGH,
                section="Length",
                issue=f"Resume too short ({word_count} words)",
                suggestion="Add more detail to your experience and achievements (aim for 400-800 words)"
            ))
        elif word_count > 1200:
            issues.append(QualityIssue(
                category=QualityCategory.STRUCTURE,
                severity=IssueSeverity.LOW,
                section="Length",
                issue=f"Resume may be too long ({word_count} words)",
                suggestion="Consider condensing to 1-2 pages (600-800 words ideal)"
            ))
        
        # Check for bullet points (good structure indicator)
        bullet_count = len(re.findall(r'^[\s]*[-•*]\s', resume_text, re.MULTILINE))
        if bullet_count < 5:
            issues.append(QualityIssue(
                category=QualityCategory.STRUCTURE,
                severity=IssueSeverity.LOW,
                section="Formatting",
                issue="Few bullet points found",
                suggestion="Use bullet points for achievements - easier to scan"
            ))
        
        return issues
    
    def _analyze_content(
        self,
        resume_text: str,
        resume_document: Optional[ResumeDocument] = None,
    ) -> List[QualityIssue]:
        """
        Analyze content quality using RULE-BASED checks (no LLM = consistent results).
        """
        import re
        issues = []
        resume_lower = resume_text.lower()
        
        # Check for passive voice indicators
        passive_indicators = ['was responsible for', 'were responsible for', 'was involved in', 
                             'were involved in', 'was tasked with', 'duties included']
        passive_found = [p for p in passive_indicators if p in resume_lower]
        if passive_found:
            issues.append(QualityIssue(
                category=QualityCategory.CONTENT,
                severity=IssueSeverity.MEDIUM,
                section="Language",
                issue=f"Passive voice detected: '{passive_found[0]}'",
                suggestion="Replace with active voice: 'Led...', 'Managed...', 'Developed...'",
                example="Instead of 'Was responsible for deployment', write 'Led deployment of...'"
            ))
        
        # Check for first-person pronouns (should be avoided in resumes)
        first_person = len(re.findall(r'\b(I|my|me|we|our)\b', resume_text, re.IGNORECASE))
        if first_person > 3:
            issues.append(QualityIssue(
                category=QualityCategory.CONTENT,
                severity=IssueSeverity.LOW,
                section="Language",
                issue=f"Found {first_person} first-person pronouns",
                suggestion="Remove 'I', 'my', 'me' - start bullets with action verbs instead"
            ))
        
        # Check for generic/vague terms
        vague_terms = ['various', 'several', 'many', 'numerous', 'some', 'etc', 'and more']
        vague_found = [t for t in vague_terms if t in resume_lower]
        if vague_found:
            issues.append(QualityIssue(
                category=QualityCategory.CONTENT,
                severity=IssueSeverity.LOW,
                section="Specificity",
                issue=f"Vague term found: '{vague_found[0]}'",
                suggestion="Be specific - replace 'various projects' with exact number or names"
            ))
        
        # Check sentence length on structured entries instead of the entire raw blob
        long_entries = []
        if resume_document:
            long_entries = [
                entry for entry in resume_document.iter_entries()
                if len(entry.text.split()) > 35
            ]
        else:
            sentences = re.split(r'[.!?]', resume_text)
            long_entries = [s.strip() for s in sentences if len(s.split()) > 35]

        if long_entries:
            target_text = long_entries[0].text if resume_document else long_entries[0]
            target_entry_id = long_entries[0].id if resume_document else None
            issues.append(QualityIssue(
                category=QualityCategory.CONTENT,
                severity=IssueSeverity.LOW,
                section="Readability",
                issue=f"Found {len(long_entries)} overly long sentence(s)",
                suggestion="Break long sentences into shorter, punchy bullet points",
                target_text=target_text,
                target_entry_id=target_entry_id,
            ))
        
        return issues
    
    def _analyze_content_llm(self, resume_text: str) -> List[QualityIssue]:
        """
        LLM-based content analysis (DEPRECATED - use rule-based instead).
        Kept for reference but not called.
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        import json
        import re
        
        prompt = SystemMessage(content="""You are a RESUME CONTENT ANALYZER. Check for content quality issues.

CHECK FOR:
1. Weak action verbs (helped, worked on, assisted)
2. Vague descriptions without specifics
3. Passive voice instead of active voice
4. Too much jargon or too little technical detail
5. Responsibilities without achievements
6. Spelling/grammar issues (if obvious)

Respond with JSON only:
{
    "issues": [
        {
            "section": "Experience - Company X",
            "issue": "Uses weak action verb 'helped'",
            "suggestion": "Replace 'Helped with deployment' with 'Led deployment of...' or 'Deployed...'",
            "example": "Led deployment of microservices to production, reducing downtime by 40%",
            "severity": "medium"
        }
    ]
}""")
        
        human_prompt = HumanMessage(content=f"""Analyze this resume for content quality issues:

{resume_text[:3000]}""")
        
        try:
            response = self.llm_service.invoke_with_retry([prompt, human_prompt])
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                issues = []
                for item in data.get("issues", []):
                    issues.append(QualityIssue(
                        category=QualityCategory.CONTENT,
                        severity=IssueSeverity[item.get("severity", "medium").upper()],
                        section=item.get("section", "General"),
                        issue=item.get("issue", ""),
                        suggestion=item.get("suggestion", ""),
                        example=item.get("example")
                    ))
                return issues
        except Exception as e:
            logger.error(f"Content analysis failed: {e}")
        
        return []
    
    def _analyze_impact(self, resume_text: str) -> List[QualityIssue]:
        """Analyze impact/metrics - quantification, achievements"""
        from langchain_core.messages import SystemMessage, HumanMessage
        import json
        import re
        
        prompt = SystemMessage(content="""You are a RESUME IMPACT ANALYZER. Check for missing metrics and achievements.

CHECK FOR:
1. Bullet points without numbers/percentages
2. Achievements not quantified (how much? how many? how fast?)
3. No mention of scale (team size, user count, revenue impact)
4. Missing before/after comparisons
5. No business impact mentioned

Respond with JSON only:
{
    "issues": [
        {
            "section": "Experience - Company X",
            "issue": "Achievement 'Improved performance' lacks metrics",
            "suggestion": "Quantify the improvement: 'Improved performance by X%' or 'Reduced latency from Xms to Yms'",
            "severity": "high"
        }
    ]
}""")
        
        human_prompt = HumanMessage(content=f"""Analyze this resume for missing metrics and impact:

{resume_text[:3000]}""")
        
        try:
            response = self.llm_service.invoke_with_retry([prompt, human_prompt])
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                issues = []
                for item in data.get("issues", []):
                    issues.append(QualityIssue(
                        category=QualityCategory.IMPACT,
                        severity=IssueSeverity[item.get("severity", "medium").upper()],
                        section=item.get("section", "General"),
                        issue=item.get("issue", ""),
                        suggestion=item.get("suggestion", "")
                    ))
                return issues
        except Exception as e:
            logger.error(f"Impact analysis failed: {e}")
        
        return []
    
    def _calculate_score(self, issues: List[QualityIssue], resume_text: str) -> int:
        """Calculate overall quality score based on issues found"""
        # Start with base score
        score = 85
        
        # Deduct for issues
        for issue in issues:
            if issue.severity == IssueSeverity.HIGH:
                score -= 10
            elif issue.severity == IssueSeverity.MEDIUM:
                score -= 5
            else:
                score -= 2
        
        # Bonus for length
        word_count = len(resume_text.split())
        if 400 <= word_count <= 800:
            score += 5
        
        # Ensure score is in range
        return max(0, min(100, score))
    
    def _identify_strengths(self, resume_text: str) -> List[str]:
        """Identify resume strengths"""
        strengths = []
        
        # Check for common positive indicators
        resume_lower = resume_text.lower()
        
        if any(word in resume_lower for word in ['%', 'increased', 'decreased', 'improved', 'reduced']):
            strengths.append("Contains quantified achievements")
        
        if any(word in resume_lower for word in ['led', 'managed', 'directed', 'spearheaded']):
            strengths.append("Uses strong action verbs")
        
        if any(section in resume_lower for section in ['summary', 'professional summary', 'objective']):
            strengths.append("Has a professional summary")
        
        if any(section in resume_lower for section in ['skills', 'technical skills', 'technologies']):
            strengths.append("Has a dedicated skills section")
        
        return strengths
    
    def _prioritize_improvements(self, issues: List[QualityIssue]) -> List[str]:
        """Prioritize which improvements to make first"""
        priorities = []
        
        # High severity first
        high_issues = [i for i in issues if i.severity == IssueSeverity.HIGH]
        if high_issues:
            categories = set(i.category.value for i in high_issues)
            for cat in categories:
                priorities.append(f"Fix {cat} issues (HIGH priority)")
        
        # Structure issues are important
        structure_issues = [i for i in issues if i.category == QualityCategory.STRUCTURE]
        if structure_issues and "Fix structure issues (HIGH priority)" not in priorities:
            priorities.append("Improve resume structure")
        
        # Impact/metrics are very valuable
        impact_issues = [i for i in issues if i.category == QualityCategory.IMPACT]
        if impact_issues:
            priorities.append("Add metrics and quantify achievements")
        
        return priorities[:5]  # Top 5 priorities
    
    def _estimate_impact(self, current_score: int, issues: List[QualityIssue]) -> str:
        """Estimate the impact of fixing issues"""
        high_count = sum(1 for i in issues if i.severity == IssueSeverity.HIGH)
        
        if current_score >= 80:
            return "Your resume is already strong. Minor improvements possible."
        elif current_score >= 60:
            return f"Fixing {high_count} high-priority issues could significantly improve your response rate."
        else:
            return f"Your resume needs work. Addressing these issues could dramatically improve your job search success."
    
    def _apply_improvements_with_context(
        self,
        resume_text: str,
        issues: List[QualityIssue],
        user_answers: Dict[str, Any],
        resolution_guidance: Optional[List[str]] = None,
    ) -> Tuple[str, List[str], int, Dict[str, Any]]:
        """
        Apply improvements using user-provided context to ensure accuracy.
        This prevents fabrication by using actual user data.
        """
        from langchain_core.messages import SystemMessage, HumanMessage

        deterministic_text, deterministic_changes, fixed_issue_ids, remaining_issues = self._apply_deterministic_issue_fixes(
            resume_text,
            issues,
        )
        if not remaining_issues:
            original_metric_set = normalize_metric_set(extract_metrics(resume_text))
            final_metric_set = normalize_metric_set(extract_metrics(deterministic_text))
            return deterministic_text, deterministic_changes, max(0, len(final_metric_set) - len(original_metric_set)), {
                "llm_used": False,
                "deterministic_fix_applied": True,
                "deterministic_fixed_issue_ids": fixed_issue_ids,
                "remaining_issue_ids": [],
            }
        
        # Build improvement instructions with SPECIFIC findings so the LLM knows exactly what to fix
        improvements = []
        for issue in remaining_issues[:10]:  # Limit to top 10 issues
            specific = issue.issue.strip()
            suggestion = issue.suggestion.strip()
            example = getattr(issue, "example", None) and str(issue.example).strip()
            target_text = getattr(issue, "target_text", None) and str(issue.target_text).strip()
            if specific and suggestion:
                line = f"- {issue.section}: {specific} → {suggestion}"
                if target_text:
                    line += f" | Target line: {target_text}"
                if example:
                    line += f" (e.g. {example})"
                improvements.append(line)
            else:
                improvements.append(f"- {issue.section}: {suggestion}")
        improvements_text = "\n".join(improvements)

        user_context = self._build_user_context(user_answers)
        extra_guidance = ""
        if resolution_guidance:
            extra_guidance = "\n\nUSER-APPROVED ISSUE DIRECTIONS:\n" + "\n".join(f"- {item}" for item in resolution_guidance)
        
        # Truncate resume if too long to avoid token limits
        max_resume_chars = 8000
        truncated_resume = deterministic_text[:max_resume_chars] if len(deterministic_text) > max_resume_chars else deterministic_text
        
        prompt = SystemMessage(content=f"""You are a RESUME IMPROVER. Apply ONLY the listed improvements; do not add new changes beyond those.

IMPROVEMENTS TO MAKE (only these):
{improvements_text}
{user_context}{extra_guidance}

ABSOLUTE REQUIREMENTS:
1. Output the ENTIRE resume from start to finish - DO NOT STOP EARLY
2. Include EVERY job, EVERY bullet point, EVERY section from the original
3. If the original has 4 jobs, output 4 jobs. If it has 20 bullet points, output 20 bullet points.
4. DO NOT truncate, summarize, or shorten ANY section
5. The output length should be SIMILAR to the input length
6. ONLY fix the issues listed above (e.g. replace weak verbs, break long sentences into bullets). Do not change formatting, add/remove sections, or introduce new issues.

FORMAT REQUIREMENTS:
- Use **bold** for section headers (e.g., **WORK EXPERIENCE**)
- Use **bold** for job titles with company and dates (e.g., **Senior Engineer, Company Name, Jan 2020 - Present**)
- Use bullet points (• ) for achievements - use the actual bullet character •
- Keep clean spacing between sections

CONTENT RULES:
- Preserve ALL original facts - DO NOT fabricate
- DO NOT change job titles, company names, or dates
- DO NOT invent specific numbers like "10 services", "15 tests", "20 APIs" unless the user provided them
- Only add metrics/percentages if user provided them OR they were in the original
- If no numbers are provided, use qualitative language instead of adding numbers
- Use strong action verbs but keep the original scope
- Treat user-provided notes as guidance, not paste-ready resume text
- DO NOT copy any user-provided answer verbatim into the resume unless it already exists in the original resume
- Refine user guidance into concise resume language and integrate it into existing bullets or summary lines only when it fits cleanly

START OUTPUT IMMEDIATELY - no preamble.""")
        
        human_prompt = HumanMessage(content=f"""RESUME TO IMPROVE (output the COMPLETE improved version):

{truncated_resume}

OUTPUT THE ENTIRE IMPROVED RESUME NOW:""")
        
        try:
            improved = self.llm_service.invoke_with_retry([prompt, human_prompt], use_cache=False)
            llm_metadata = dict(getattr(self.llm_service, "last_invoke_metadata", {}) or {})
            
            # Clean response
            improved = improved.strip()
            
            # Remove markdown code blocks
            if improved.startswith("```"):
                lines = improved.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                improved = "\n".join(lines)
            
            # Remove common LLM preambles
            preambles = [
                "Here is the improved resume:",
                "Here's the improved resume:",
                "Here is the enhanced resume:",
                "Below is the improved resume:",
                "The improved resume:",
            ]
            for preamble in preambles:
                if improved.lower().startswith(preamble.lower()):
                    improved = improved[len(preamble):].strip()

            improved = self._normalize_resume_layout(improved)
            
            # Validate response - if too short, return original
            if len(improved) < len(resume_text) * 0.5:
                logger.warning(
                    "Improved resume too short, returning original",
                    original_len=len(resume_text),
                    improved_len=len(improved)
                )
                return deterministic_text, deterministic_changes + ["Improvement failed - output was truncated"], 0, {
                    "llm_used": True,
                    "llm_metadata": llm_metadata,
                    "deterministic_fix_applied": bool(fixed_issue_ids),
                    "deterministic_fixed_issue_ids": fixed_issue_ids,
                    "remaining_issue_ids": [issue.id for issue in remaining_issues],
                    "fallback_reason": "truncated_output",
                }
            
            # Remove any unverified metrics introduced by the LLM
            original_metric_set = normalize_metric_set(extract_metrics(resume_text))
            user_metric_set = normalize_metric_set(extract_metrics_from_user_answers(user_answers))
            allowed_metrics = {**original_metric_set, **user_metric_set}
            improved_metrics = extract_metrics(improved)
            unverified_metrics = [
                metric for metric in improved_metrics
                if metric.normalized not in allowed_metrics
            ]
            if unverified_metrics:
                unique_unverified = sorted({m.raw for m in unverified_metrics})
                improved = self._remove_unverified_metrics(improved, unique_unverified)
                # Re-extract after cleanup
                improved_metrics = extract_metrics(improved)

            verbatim_snippets = self._find_verbatim_user_answer_snippets(
                original_resume=resume_text,
                candidate_resume=improved,
                user_answers=user_answers,
            )
            if verbatim_snippets:
                improved = self._rewrite_verbatim_user_input(improved, verbatim_snippets, resume_text)

            # Count metrics added (after cleanup)
            final_metric_set = normalize_metric_set(improved_metrics)
            metrics_added = max(0, len(final_metric_set) - len(original_metric_set))
            
            changes = deterministic_changes + [f"Applied: {issue.suggestion[:50]}..." for issue in remaining_issues[:10]]
            if unverified_metrics:
                changes.append(f"Removed unverified metrics: {len(set(m.raw for m in unverified_metrics))}")
            return improved, changes, metrics_added, {
                "llm_used": True,
                "llm_metadata": llm_metadata,
                "deterministic_fix_applied": bool(fixed_issue_ids),
                "deterministic_fixed_issue_ids": fixed_issue_ids,
                "remaining_issue_ids": [issue.id for issue in remaining_issues],
                "output_cleaned": True,
                "unverified_metrics_removed": len(set(m.raw for m in unverified_metrics)) if unverified_metrics else 0,
            }
            
        except Exception as e:
            logger.error(f"Resume improvement failed: {e}")
            return deterministic_text, deterministic_changes, 0, {
                "llm_used": True,
                "llm_metadata": dict(getattr(self.llm_service, "last_invoke_metadata", {}) or {}),
                "deterministic_fix_applied": bool(fixed_issue_ids),
                "deterministic_fixed_issue_ids": fixed_issue_ids,
                "remaining_issue_ids": [issue.id for issue in remaining_issues],
                "fallback_reason": str(e),
            }

    def _apply_deterministic_issue_fixes(
        self,
        resume_text: str,
        issues: List[QualityIssue],
    ) -> Tuple[str, List[str], List[str], List[QualityIssue]]:
        """Apply safe, deterministic fixes for mechanical issues before using the LLM."""
        updated_text = resume_text
        resume_document = parse_resume_document(resume_text)
        changes: List[str] = []
        fixed_issue_ids: List[str] = []
        remaining_issues: List[QualityIssue] = []

        for issue in issues:
            next_text = updated_text
            issue_text_lower = issue.issue.lower()

            if "vague term found" in issue_text_lower:
                next_text = self._remove_vague_terms(next_text)
            elif "passive voice detected" in issue_text_lower:
                next_text = self._replace_passive_voice(next_text)
            elif "overly long sentence" in issue_text_lower:
                next_text = self._split_long_entries_in_document(
                    resume_document,
                    issue.target_entry_id,
                    issue.target_text,
                )
            elif issue.category == QualityCategory.METRICS and issue.advisory_only:
                next_text = self._tighten_qualitative_impact(
                    resume_document,
                    issue.target_entry_id,
                    issue.target_text,
                )

            if next_text != updated_text:
                updated_text = next_text
                resume_document = parse_resume_document(updated_text)
                fixed_issue_ids.append(issue.id)
                changes.append(f"Applied deterministic fix: {issue.suggestion[:60]}...")
            else:
                remaining_issues.append(issue)

        return updated_text, changes, fixed_issue_ids, remaining_issues

    def _remove_vague_terms(self, resume_text: str) -> str:
        replacements = {
            r"\betc\b": "",
            r"\bvarious\b": "multiple",
            r"\bseveral\b": "multiple",
            r"\bnumerous\b": "multiple",
            r"\band more\b": "",
        }
        updated = resume_text
        for pattern, replacement in replacements.items():
            updated = re.sub(pattern, replacement, updated, flags=re.IGNORECASE)
            updated = re.sub(r"[ \t]{2,}", " ", updated)
            updated = re.sub(r"\s+,", ",", updated)
        return self._normalize_resume_layout(updated)

    def _replace_passive_voice(self, resume_text: str) -> str:
        replacements = {
            "was responsible for": "Led",
            "were responsible for": "Led",
            "was involved in": "Contributed to",
            "were involved in": "Contributed to",
            "was tasked with": "Executed",
            "duties included": "Delivered",
        }
        updated = resume_text
        for source, target in replacements.items():
            updated = re.sub(rf"\b{re.escape(source)}\b", target, updated, flags=re.IGNORECASE)
        return self._normalize_resume_layout(updated)

    def _split_long_bullets(self, resume_text: str) -> str:
        lines = resume_text.splitlines()
        updated_lines: List[str] = []
        changed = False

        for line in lines:
            stripped = line.strip()
            bullet_match = re.match(r"^(\s*[-•*]\s+)(.+)$", line)
            if not bullet_match:
                updated_lines.append(line)
                continue

            prefix, content = bullet_match.groups()
            if len(stripped.split()) <= 35:
                updated_lines.append(line)
                continue

            separators = list(re.finditer(r",\s+|;\s+|\s+and\s+", content))
            if not separators:
                updated_lines.append(line)
                continue

            midpoint = len(content) / 2
            split_match = min(separators, key=lambda match: abs(match.start() - midpoint))
            first = content[:split_match.start()].strip(" ,;")
            second = content[split_match.end():].strip(" ,;")
            if len(first.split()) < 6 or len(second.split()) < 6:
                updated_lines.append(line)
                continue

            updated_lines.append(f"{prefix}{first}")
            updated_lines.append(f"{prefix}{second[:1].upper()}{second[1:]}")
            changed = True

        return self._normalize_resume_layout("\n".join(updated_lines)) if changed else resume_text

    def _split_long_entries_in_document(
        self,
        resume_document: ResumeDocument,
        target_entry_id: Optional[str],
        target_text: Optional[str],
    ) -> str:
        """Split a single long entry using structured resume nodes."""
        if not target_text and not target_entry_id:
            return resume_document.render()

        target_entry = None
        if target_entry_id:
            target_entry = resume_document.find_entry_by_id(target_entry_id)
        if not target_entry and target_text:
            target_entry = resume_document.find_entry_by_text(target_text)
        if not target_entry or len(target_entry.text.split()) <= 35:
            return resume_document.render()

        split_parts = self._split_entry_text(target_entry.text)
        if len(split_parts) < 2:
            return resume_document.render()

        for section in resume_document.sections:
            rebuilt_entries = []
            for entry in section.entries:
                if entry.id != target_entry.id:
                    rebuilt_entries.append(entry)
                    continue

                for idx, part in enumerate(split_parts):
                    prefix = "• " if entry.kind == "bullet" else ""
                    rebuilt_entries.append(
                        type(entry)(
                            id=f"{entry.id}-{idx}",
                            section_name=entry.section_name,
                            kind=entry.kind,
                            text=f"{prefix}{part}" if prefix and not part.startswith("• ") else part,
                        )
                    )
            section.entries = rebuilt_entries

        return self._normalize_resume_layout(resume_document.render())

    def _split_entry_text(self, text: str) -> List[str]:
        normalized = re.sub(r"^\s*[-•*]\s+", "", text).strip()
        sentence_parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]
        if len(sentence_parts) >= 2:
            return sentence_parts

        separators = list(re.finditer(r",\s+|;\s+|\s+and\s+", normalized))
        if not separators:
            return [normalized]

        midpoint = len(normalized) / 2
        split_match = min(separators, key=lambda match: abs(match.start() - midpoint))
        first = normalized[:split_match.start()].strip(" ,;")
        second = normalized[split_match.end():].strip(" ,;")
        if len(first.split()) < 6 or len(second.split()) < 6:
            return [normalized]
        return [first, f"{second[:1].upper()}{second[1:]}" if second else second]

    def _tighten_qualitative_impact(
        self,
        resume_document: ResumeDocument,
        target_entry_id: Optional[str],
        target_text: Optional[str],
    ) -> str:
        """Improve impact wording without inventing numbers."""
        target_entry = None
        if target_entry_id:
            target_entry = resume_document.find_entry_by_id(target_entry_id)
        if not target_entry and target_text:
            target_entry = resume_document.find_entry_by_text(target_text)
        if not target_entry:
            return resume_document.render()

        rewritten = target_entry.text
        replacements = [
            ("resulting in reduction of", "reducing"),
            ("resulting in substantial reduction of", "reducing"),
            ("helped", "supported"),
            ("worked on", "delivered"),
        ]
        for source, replacement in replacements:
            rewritten = re.sub(rf"\b{re.escape(source)}\b", replacement, rewritten, flags=re.IGNORECASE)

        rewritten = re.sub(r"\bsubstantial\s+reduction\b", "measurable reduction", rewritten, flags=re.IGNORECASE)
        rewritten = re.sub(r"\bimproved\b", "strengthened", rewritten, flags=re.IGNORECASE)

        if rewritten == target_entry.text:
            return resume_document.render()

        for section in resume_document.sections:
            for entry in section.entries:
                if entry.id == target_entry.id:
                    entry.text = rewritten
                    return self._normalize_resume_layout(resume_document.render())

        return resume_document.render()

    def _merge_issue_resolution_inputs(
        self,
        user_answers: Dict[str, Any],
        issues: List[QualityIssue],
        issue_resolutions: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Promote per-issue custom metric details into effective user answers."""
        merged = dict(user_answers or {})
        metric_inputs: List[str] = []

        issues_by_id = {issue.id: issue for issue in issues}
        for issue_id, resolution in (issue_resolutions or {}).items():
            if not isinstance(resolution, dict):
                continue
            if str(resolution.get("action") or "").strip().lower() != "custom":
                continue
            custom_text = str(resolution.get("custom_text") or "").strip()
            if not custom_text:
                continue
            issue = issues_by_id.get(issue_id)
            if not issue or issue.category != QualityCategory.METRICS:
                continue
            target = issue.target_text or issue.issue
            metric_inputs.append(f"{target}: {custom_text}")

        if metric_inputs:
            existing = str(merged.get("metrics_details") or "").strip()
            combined = "\n".join(([existing] if existing else []) + metric_inputs)
            merged["metrics_details"] = combined

        return merged

    def _build_user_context(self, user_answers: Dict[str, Any]) -> str:
        """Format user answers as structured guidance instead of paste-ready text."""
        if not user_answers:
            return ""

        context_parts = []
        if user_answers.get("metrics_by_role"):
            context_parts.append(
                f"ROLE-SPECIFIC METRICS (convert into polished bullets for the correct role; do not paste verbatim):\n{user_answers['metrics_by_role']}"
            )
        if user_answers.get("metrics_details"):
            context_parts.append(
                f"METRIC GUIDANCE (refine into resume language, not direct quotes): {user_answers['metrics_details']}"
            )
        if user_answers.get("team_size"):
            context_parts.append(f"TEAM SIZE CONTEXT: {user_answers['team_size']}")
        if user_answers.get("project_scale"):
            context_parts.append(f"PROJECT SCALE CONTEXT: {user_answers['project_scale']}")
        if user_answers.get("career_focus"):
            context_parts.append(f"TARGET ROLE/FOCUS GUIDANCE: {user_answers['career_focus']}")
        certifications = str(user_answers.get("certifications") or "").strip()
        if certifications and certifications.lower() != "none":
            context_parts.append(
                f"CERTIFICATION GUIDANCE (include only if it belongs in the resume, do not paste as a note): {certifications}"
            )
        if user_answers.get("notable_achievements"):
            context_parts.append(
                f"ACHIEVEMENT GUIDANCE (refine into concise resume bullets, not verbatim): {user_answers['notable_achievements']}"
            )

        if not context_parts:
            return ""
        return "\n\nUSER-PROVIDED GUIDANCE:\n" + "\n".join(context_parts)

    def _apply_issue_resolutions(
        self,
        issues: List[QualityIssue],
        issue_resolutions: Dict[str, Any],
    ) -> Tuple[List[QualityIssue], List[str]]:
        """Filter issues and collect user-approved per-issue guidance."""
        active_issues: List[QualityIssue] = []
        guidance: List[str] = []

        for issue in issues:
            resolution = issue_resolutions.get(issue.id, {}) if isinstance(issue_resolutions, dict) else {}
            action = str(resolution.get("action") or "approve").strip().lower()
            custom_text = str(resolution.get("custom_text") or "").strip()

            if action == "skip":
                continue

            active_issues.append(issue)
            if action == "custom" and custom_text:
                guidance.append(
                    f"For issue '{issue.issue}', use this user-approved language direction instead of the default fix: {custom_text}"
                )
            elif action == "approve" and issue.proposed_fix:
                guidance.append(
                    f"For issue '{issue.issue}', follow this approved fix direction: {issue.proposed_fix}"
                )

        return active_issues, guidance

    def _find_verbatim_user_answer_snippets(
        self,
        original_resume: str,
        candidate_resume: str,
        user_answers: Dict[str, Any],
    ) -> List[str]:
        """Detect long user-provided snippets copied directly into the generated resume."""
        if not user_answers:
            return []

        original_normalized = re.sub(r"\s+", " ", original_resume).strip().lower()
        candidate_normalized = re.sub(r"\s+", " ", candidate_resume).strip().lower()
        suspicious_snippets: List[str] = []

        for value in user_answers.values():
            if not isinstance(value, str):
                continue
            for chunk in re.split(r"[\n;]", value):
                normalized_chunk = re.sub(r"\s+", " ", chunk).strip()
                if len(normalized_chunk) < 24:
                    continue
                lowered = normalized_chunk.lower()
                if lowered in original_normalized:
                    continue
                if lowered in candidate_normalized:
                    suspicious_snippets.append(normalized_chunk)

        return sorted(set(suspicious_snippets))

    def _rewrite_verbatim_user_input(
        self,
        candidate_resume: str,
        verbatim_snippets: List[str],
        original_resume: str,
    ) -> str:
        """Remove direct copy-paste from user notes while preserving the intended improvement."""
        if not verbatim_snippets:
            return candidate_resume

        from langchain_core.messages import SystemMessage, HumanMessage

        snippets_text = "\n".join(f"- {snippet}" for snippet in verbatim_snippets)
        prompt = SystemMessage(content=f"""You are a RESUME EDITOR.

The generated resume copied user notes too directly. Rewrite the affected lines so the resume sounds polished and professional.

COPIED SNIPPETS TO ELIMINATE:
{snippets_text}

RULES:
1. Do not paste or quote these snippets verbatim
2. Preserve the underlying facts if they fit the resume
3. Keep the same resume structure and sections
4. Do not add new facts
5. Return the full updated resume only""")

        human_prompt = HumanMessage(content=f"""Original resume for fact grounding:
---
{original_resume[:3000]}

Generated resume to clean up:
---
{candidate_resume}
""")

        try:
            cleaned = self.llm_service.invoke_with_retry([prompt, human_prompt]).strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()

            if len(cleaned) < len(candidate_resume) * 0.7:
                logger.warning("Verbatim cleanup too aggressive; keeping pre-cleaned candidate")
                return candidate_resume
            return cleaned
        except Exception as e:
            logger.warning(f"Verbatim cleanup failed: {e}")
            return candidate_resume
    
    def _apply_improvements(
        self,
        resume_text: str,
        issues: List[QualityIssue]
    ) -> Tuple[str, List[str]]:
        """Legacy method - calls new method without user context"""
        improved, changes, _ = self._apply_improvements_with_context(resume_text, issues, {})
        return improved, changes

    def _remove_unverified_metrics(self, resume_text: str, metrics: List[str]) -> str:
        """Remove or soften metrics that were not verified."""
        if not metrics:
            return resume_text

        from langchain_core.messages import SystemMessage, HumanMessage

        metrics_text = "\n".join(f"- {m}" for m in metrics)

        prompt = SystemMessage(content=f"""You are a RESUME METRIC CLEANER.

METRICS TO REMOVE OR SOFTEN:
{metrics_text}

RULES:
1. Remove the numeric values for these metrics or rewrite the line qualitatively without numbers
2. Do NOT remove valid numbers that are not listed
3. Preserve formatting and structure
4. Do NOT add new information

Return ONLY the modified resume text.""")

        human_prompt = HumanMessage(content=f"""Resume to fix:
---
{resume_text}
---

Remove or soften ONLY the listed metrics. Return only the fixed resume.""")

        try:
            cleaned = self.llm_service.invoke_with_retry([prompt, human_prompt]).strip()

            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()

            # Safety: if output is too short, return original
            if len(cleaned) < len(resume_text) * 0.7:
                logger.warning(
                    "Metric cleanup too aggressive; returning original",
                    original_len=len(resume_text),
                    cleaned_len=len(cleaned)
                )
                return resume_text

            return cleaned
        except Exception as e:
            logger.warning(f"Metric cleanup failed: {e}")
            return resume_text

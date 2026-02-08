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

from ..services.llm_service import LLMService
from ..utils.logger import logger
from ..utils.metrics import extract_metrics, extract_metrics_from_user_answers, normalize_metric_set


# Research-backed quality criteria weights
QUALITY_WEIGHTS = {
    "quantified_achievements": 25,  # 40% increase in hireability
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
    example: Optional[str] = None  # Example of improved text
    research_note: Optional[str] = None  # Why this matters (research-backed)


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


@dataclass
class ImprovedResume:
    """Result of improving a resume"""
    improved_text: str
    changes_made: List[str]
    before_score: int
    after_score: int
    metrics_added: int = 0  # How many achievements were quantified


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
    
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
    
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
        
        # Run specialized analyses
        ats_issues, ats_score = self._analyze_ats_compatibility(resume_text)
        metrics_issues, metrics_count = self._analyze_metrics(resume_text)
        verb_issues = self._analyze_action_verbs(resume_text)
        structure_issues = self._analyze_structure(resume_text)
        content_issues = self._analyze_content(resume_text)
        
        all_issues = ats_issues + metrics_issues + verb_issues + structure_issues + content_issues
        
        # Calculate weighted score based on research
        overall_score = self._calculate_research_based_score(
            ats_score, metrics_count, all_issues, resume_text
        )
        
        # Identify strengths
        strengths = self._identify_strengths(resume_text)
        
        # Prioritize based on research impact
        priority = self._prioritize_by_impact(all_issues)
        
        # Generate clarifying questions for improvement
        questions = self._generate_clarifying_questions(resume_text, all_issues)
        
        # Estimate impact with research data
        impact = self._estimate_research_backed_impact(overall_score, metrics_count, ats_score)
        
        report = QualityReport(
            overall_score=overall_score,
            issues=all_issues,
            strengths=strengths,
            improvement_priority=priority,
            estimated_impact=impact,
            ats_score=ats_score,
            metrics_count=metrics_count,
            questions=questions
        )
        
        logger.info(
            "Resume Quality Agent: Analysis complete",
            score=overall_score,
            ats_score=ats_score,
            metrics_count=metrics_count,
            issues_found=len(all_issues)
        )
        
        return report
    
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
    
    def _analyze_metrics(self, resume_text: str) -> Tuple[List[QualityIssue], int]:
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
            issues.append(QualityIssue(
                category=QualityCategory.METRICS,
                severity=IssueSeverity.MEDIUM,
                section="General",
                issue=f"Only {metrics_count} quantified achievements found (expected {expected_metrics}+)",
                suggestion="Add numbers to more bullet points: percentages, dollar amounts, user counts, time saved",
                research_note="Data-driven accomplishments are 58% more likely to secure interviews"
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
        user_answers: Optional[Dict[str, Any]] = None
    ) -> ImprovedResume:
        """
        Improve the resume based on quality analysis and user answers.
        
        Args:
            resume_text: Original resume text
            quality_report: Optional pre-computed quality report
            fix_high_only: If True, only fix HIGH severity issues
            user_answers: User's answers to clarifying questions
            
        Returns:
            ImprovedResume with the enhanced text
        """
        logger.info("Resume Quality Agent: Starting improvement with user context")
        
        # Get quality report if not provided
        if quality_report is None:
            quality_report = self.analyze_quality(resume_text)
        
        before_score = quality_report.overall_score
        
        # Filter issues to fix
        issues_to_fix = quality_report.issues
        if fix_high_only:
            issues_to_fix = [i for i in issues_to_fix if i.severity == IssueSeverity.HIGH]
        
        if not issues_to_fix:
            logger.info("Resume Quality Agent: No issues to fix")
            return ImprovedResume(
                improved_text=resume_text,
                changes_made=[],
                before_score=before_score,
                after_score=before_score
            )
        
        # Improve the resume with user context
        improved_text, changes, metrics_added = self._apply_improvements_with_context(
            resume_text, issues_to_fix, user_answers or {}
        )
        
        # Re-analyze to get new score
        new_report = self.analyze_quality(improved_text)
        after_score = new_report.overall_score
        
        logger.info(
            "Resume Quality Agent: Improvement complete",
            before_score=before_score,
            after_score=after_score,
            changes_made=len(changes),
            metrics_added=metrics_added
        )
        
        return ImprovedResume(
            improved_text=improved_text,
            changes_made=changes,
            before_score=before_score,
            after_score=after_score,
            metrics_added=metrics_added
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
    
    def _analyze_content(self, resume_text: str) -> List[QualityIssue]:
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
        
        # Check sentence length (overly long sentences)
        sentences = re.split(r'[.!?]', resume_text)
        long_sentences = [s for s in sentences if len(s.split()) > 35]
        if long_sentences:
            issues.append(QualityIssue(
                category=QualityCategory.CONTENT,
                severity=IssueSeverity.LOW,
                section="Readability",
                issue=f"Found {len(long_sentences)} overly long sentence(s)",
                suggestion="Break long sentences into shorter, punchy bullet points"
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
        user_answers: Dict[str, Any]
    ) -> Tuple[str, List[str], int]:
        """
        Apply improvements using user-provided context to ensure accuracy.
        This prevents fabrication by using actual user data.
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        
        # Build improvement instructions
        improvements = []
        for issue in issues[:10]:  # Limit to top 10 issues
            improvements.append(f"- {issue.section}: {issue.suggestion}")
        
        improvements_text = "\n".join(improvements)
        
        # Build user context from answers
        user_context = ""
        if user_answers:
            context_parts = []
            
            # Parse role-specific metrics
            if user_answers.get("metrics_by_role"):
                context_parts.append(f"ROLE-SPECIFIC METRICS (apply to correct roles):\n{user_answers['metrics_by_role']}")
            
            # Legacy fields for backwards compatibility
            if user_answers.get("metrics_details"):
                context_parts.append(f"Specific metrics provided: {user_answers['metrics_details']}")
            if user_answers.get("team_size"):
                context_parts.append(f"Team size: {user_answers['team_size']}")
            if user_answers.get("project_scale"):
                context_parts.append(f"Project scale: {user_answers['project_scale']}")
            
            if user_answers.get("career_focus"):
                context_parts.append(f"TARGET ROLE/FOCUS: {user_answers['career_focus']}")
            if user_answers.get("certifications") and user_answers.get("certifications").lower() != 'none':
                context_parts.append(f"CERTIFICATIONS TO ADD: {user_answers['certifications']}")
            if user_answers.get("notable_achievements"):
                context_parts.append(f"NOTABLE ACHIEVEMENTS TO INCORPORATE: {user_answers['notable_achievements']}")
            
            if context_parts:
                user_context = "\n\nUSER-PROVIDED INFORMATION (use this to add accurate details):\n" + "\n".join(context_parts)
        
        # Truncate resume if too long to avoid token limits
        max_resume_chars = 8000
        truncated_resume = resume_text[:max_resume_chars] if len(resume_text) > max_resume_chars else resume_text
        
        prompt = SystemMessage(content=f"""You are a RESUME IMPROVER. Improve the resume while keeping ALL content intact.

IMPROVEMENTS TO MAKE:
{improvements_text}
{user_context}

ABSOLUTE REQUIREMENTS:
1. Output the ENTIRE resume from start to finish - DO NOT STOP EARLY
2. Include EVERY job, EVERY bullet point, EVERY section from the original
3. If the original has 4 jobs, output 4 jobs. If it has 20 bullet points, output 20 bullet points.
4. DO NOT truncate, summarize, or shorten ANY section
5. The output length should be SIMILAR to the input length

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

START OUTPUT IMMEDIATELY - no preamble.""")
        
        human_prompt = HumanMessage(content=f"""RESUME TO IMPROVE (output the COMPLETE improved version):

{truncated_resume}

OUTPUT THE ENTIRE IMPROVED RESUME NOW:""")
        
        try:
            improved = self.llm_service.invoke_with_retry([prompt, human_prompt])
            
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
            
            # Validate response - if too short, return original
            if len(improved) < len(resume_text) * 0.5:
                logger.warning(
                    "Improved resume too short, returning original",
                    original_len=len(resume_text),
                    improved_len=len(improved)
                )
                return resume_text, ["Improvement failed - output was truncated"], 0
            
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
            
            # Count metrics added (after cleanup)
            final_metric_set = normalize_metric_set(improved_metrics)
            metrics_added = max(0, len(final_metric_set) - len(original_metric_set))
            
            changes = [f"Applied: {issue.suggestion[:50]}..." for issue in issues[:10]]
            if unverified_metrics:
                changes.append(f"Removed unverified metrics: {len(set(m.raw for m in unverified_metrics))}")
            return improved, changes, metrics_added
            
        except Exception as e:
            logger.error(f"Resume improvement failed: {e}")
            return resume_text, [], 0
    
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

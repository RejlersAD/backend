"""
Soft-coded configuration for RAD AI Enhanced User Profiles
============================================================
Centralizes achievement categories, experience fields, and social media platforms
to enable easy extension without modifying core business logic.

Following RAD AI soft-coding principles.
"""

# ═════════════════════════════════════════════════════════════════════════════
# Achievement Categories — Soft-Coded
# ═════════════════════════════════════════════════════════════════════════════

ACHIEVEMENT_CATEGORIES = {
    'sports': {
        'label': 'Sports & Athletics',
        'icon': '🏆',
        'color': 'blue',
        'description': 'Athletic achievements, tournaments, championships',
        'badge_color': 'bg-blue-500',
        'bg_color': 'bg-blue-50',
        'text_color': 'text-blue-700',
        'border_color': 'border-blue-300',
    },
    'academic': {
        'label': 'Academic Excellence',
        'icon': '🎓',
        'color': 'purple',
        'description': 'Degrees, academic honors, research publications',
        'badge_color': 'bg-purple-500',
        'bg_color': 'bg-purple-50',
        'text_color': 'text-purple-700',
        'border_color': 'border-purple-300',
    },
    'professional': {
        'label': 'Professional Achievements',
        'icon': '💼',
        'color': 'green',
        'description': 'Industry awards, patents, recognitions',
        'badge_color': 'bg-green-500',
        'bg_color': 'bg-green-50',
        'text_color': 'text-green-700',
        'border_color': 'border-green-300',
    },
    'innovation': {
        'label': 'Innovation & Patents',
        'icon': '💡',
        'color': 'yellow',
        'description': 'Patents, innovations, breakthrough solutions',
        'badge_color': 'bg-yellow-500',
        'bg_color': 'bg-yellow-50',
        'text_color': 'text-yellow-700',
        'border_color': 'border-yellow-300',
    },
    'community': {
        'label': 'Community & Leadership',
        'icon': '🌟',
        'color': 'pink',
        'description': 'Volunteer work, community service, leadership roles',
        'badge_color': 'bg-pink-500',
        'bg_color': 'bg-pink-50',
        'text_color': 'text-pink-700',
        'border_color': 'border-pink-300',
    },
    'creative': {
        'label': 'Creative & Artistic',
        'icon': '🎨',
        'color': 'indigo',
        'description': 'Art, music, writing, creative works',
        'badge_color': 'bg-indigo-500',
        'bg_color': 'bg-indigo-50',
        'text_color': 'text-indigo-700',
        'border_color': 'border-indigo-300',
    },
    'genius': {
        'label': 'Genius Records & Milestones',
        'icon': '⚡',
        'color': 'red',
        'description': 'World records, extraordinary achievements, unique milestones',
        'badge_color': 'bg-red-500',
        'bg_color': 'bg-red-50',
        'text_color': 'text-red-700',
        'border_color': 'border-red-300',
    },
}

# Achievement levels for gamification
ACHIEVEMENT_LEVELS = [
    {'value': 'bronze', 'label': 'Bronze', 'icon': '🥉', 'color': '#CD7F32'},
    {'value': 'silver', 'label': 'Silver', 'icon': '🥈', 'color': '#C0C0C0'},
    {'value': 'gold', 'label': 'Gold', 'icon': '🥇', 'color': '#FFD700'},
    {'value': 'platinum', 'label': 'Platinum', 'icon': '💎', 'color': '#E5E4E2'},
    {'value': 'legendary', 'label': 'Legendary', 'icon': '👑', 'color': '#9C27B0'},
]

# ═════════════════════════════════════════════════════════════════════════════
# Social Media Platforms — Soft-Coded
# ═════════════════════════════════════════════════════════════════════════════

SOCIAL_MEDIA_PLATFORMS = {
    'linkedin': {
        'label': 'LinkedIn',
        'icon': 'linkedin',
        'color': '#0A66C2',
        'placeholder': 'https://linkedin.com/in/username',
        'base_url': 'https://linkedin.com/in/',
        'validation_pattern': r'^https?://(www\.)?linkedin\.com/in/[\w-]+/?$',
    },
    'github': {
        'label': 'GitHub',
        'icon': 'github',
        'color': '#181717',
        'placeholder': 'https://github.com/username',
        'base_url': 'https://github.com/',
        'validation_pattern': r'^https?://(www\.)?github\.com/[\w-]+/?$',
    },
    'twitter': {
        'label': 'Twitter / X',
        'icon': 'twitter',
        'color': '#1DA1F2',
        'placeholder': 'https://twitter.com/username',
        'base_url': 'https://twitter.com/',
        'validation_pattern': r'^https?://(www\.)?(twitter\.com|x\.com)/[\w]+/?$',
    },
    'researchgate': {
        'label': 'ResearchGate',
        'icon': 'book-open',
        'color': '#00D0AF',
        'placeholder': 'https://researchgate.net/profile/name',
        'base_url': 'https://researchgate.net/profile/',
        'validation_pattern': r'^https?://(www\.)?researchgate\.net/profile/[\w-]+/?$',
    },
    'orcid': {
        'label': 'ORCID',
        'icon': 'id-card',
        'color': '#A6CE39',
        'placeholder': 'https://orcid.org/0000-0000-0000-0000',
        'base_url': 'https://orcid.org/',
        'validation_pattern': r'^https?://(www\.)?orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[0-9X]/?$',
    },
    'scholar': {
        'label': 'Google Scholar',
        'icon': 'graduation-cap',
        'color': '#4285F4',
        'placeholder': 'https://scholar.google.com/citations?user=ID',
        'base_url': 'https://scholar.google.com/citations?user=',
        'validation_pattern': r'^https?://scholar\.google\.com/citations\?user=[\w-]+.*$',
    },
    'medium': {
        'label': 'Medium',
        'icon': 'pen-tool',
        'color': '#000000',
        'placeholder': 'https://medium.com/@username',
        'base_url': 'https://medium.com/@',
        'validation_pattern': r'^https?://(www\.)?medium\.com/@[\w-]+/?$',
    },
    'youtube': {
        'label': 'YouTube',
        'icon': 'youtube',
        'color': '#FF0000',
        'placeholder': 'https://youtube.com/@username',
        'base_url': 'https://youtube.com/',
        'validation_pattern': r'^https?://(www\.)?youtube\.com/(@|c/|channel/)[\w-]+/?$',
    },
    'website': {
        'label': 'Personal Website',
        'icon': 'globe',
        'color': '#6B7280',
        'placeholder': 'https://yourwebsite.com',
        'base_url': '',
        'validation_pattern': r'^https?://[\w\-\.]+\.\w+.*$',
    },
}

# ═════════════════════════════════════════════════════════════════════════════
# Experience & Career Configuration
# ═════════════════════════════════════════════════════════════════════════════

EMPLOYMENT_TYPES = [
    {'value': 'full_time', 'label': 'Full-Time'},
    {'value': 'part_time', 'label': 'Part-Time'},
    {'value': 'contract', 'label': 'Contract'},
    {'value': 'freelance', 'label': 'Freelance'},
    {'value': 'internship', 'label': 'Internship'},
    {'value': 'consulting', 'label': 'Consulting'},
]

# Industry sectors (Oil & Gas specific + general)
INDUSTRY_SECTORS = [
    'Upstream Oil & Gas',
    'Midstream Oil & Gas',
    'Downstream Oil & Gas',
    'Petrochemical',
    'Refining',
    'LNG & Gas Processing',
    'Power Generation',
    'Renewable Energy',
    'Chemical Processing',
    'Manufacturing',
    'Engineering Consulting',
    'EPC Contracting',
    'Operations & Maintenance',
    'Academia & Research',
    'Government',
    'Technology',
]

# ═════════════════════════════════════════════════════════════════════════════
# Profile Gamification & Badges
# ═════════════════════════════════════════════════════════════════════════════

PROFILE_BADGES = {
    'early_adopter': {
        'label': 'Early Adopter',
        'icon': '🚀',
        'description': 'Joined RAD AI in the early days',
        'color': 'purple',
    },
    'profile_complete': {
        'label': 'Profile Master',
        'icon': '✅',
        'description': '100% profile completion',
        'color': 'green',
    },
    'achievement_hunter': {
        'label': 'Achievement Hunter',
        'icon': '🏅',
        'description': 'Added 5+ achievements',
        'color': 'yellow',
    },
    'veteran': {
        'label': 'Industry Veteran',
        'icon': '🎖️',
        'description': '20+ years of experience',
        'color': 'red',
    },
    'certified_pro': {
        'label': 'Certified Professional',
        'icon': '📜',
        'description': '5+ professional certifications',
        'color': 'blue',
    },
    'connected': {
        'label': 'Well Connected',
        'icon': '🌐',
        'description': 'Added 3+ social media links',
        'color': 'cyan',
    },
}

# Profile completion weights (updated to include new sections)
PROFILE_COMPLETION_WEIGHTS = {
    'basic_info': 20,        # Name, photo, contact
    'bio': 10,               # Bio section
    'professional': 15,      # Department, job title, expertise
    'skills': 15,            # Technical skills
    'certifications': 10,    # Professional certifications
    'experience': 15,        # Work experience entries
    'achievements': 10,      # Achievement entries
    'social_links': 5,       # Social media links
}

# ═════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═════════════════════════════════════════════════════════════════════════════

def get_achievement_category(category_code):
    """Return achievement category configuration by code."""
    return ACHIEVEMENT_CATEGORIES.get(category_code, ACHIEVEMENT_CATEGORIES['professional'])

def get_social_platform(platform_code):
    """Return social media platform configuration by code."""
    return SOCIAL_MEDIA_PLATFORMS.get(platform_code, SOCIAL_MEDIA_PLATFORMS['website'])

def calculate_profile_completeness(profile_data):
    """
    Calculate profile completeness percentage based on filled sections.
    Returns a value between 0-100.
    """
    score = 0
    
    # Basic info (20%)
    basic_fields = ['first_name', 'last_name', 'email', 'profile_photo']
    basic_filled = sum(1 for f in basic_fields if profile_data.get(f))
    score += (basic_filled / len(basic_fields)) * PROFILE_COMPLETION_WEIGHTS['basic_info']
    
    # Bio (10%)
    if profile_data.get('bio') and len(profile_data['bio']) > 50:
        score += PROFILE_COMPLETION_WEIGHTS['bio']
    
    # Professional (15%)
    prof_fields = ['department', 'job_title', 'engineer_profile']
    prof_filled = sum(1 for f in prof_fields if profile_data.get(f))
    score += (prof_filled / len(prof_fields)) * PROFILE_COMPLETION_WEIGHTS['professional']
    
    # Skills (15%)
    skills = profile_data.get('engineer_profile', {}).get('technical_skills', [])
    if len(skills) >= 5:
        score += PROFILE_COMPLETION_WEIGHTS['skills']
    elif len(skills) > 0:
        score += (len(skills) / 5) * PROFILE_COMPLETION_WEIGHTS['skills']
    
    # Certifications (10%)
    certs = profile_data.get('engineer_profile', {}).get('certifications', [])
    if len(certs) >= 3:
        score += PROFILE_COMPLETION_WEIGHTS['certifications']
    elif len(certs) > 0:
        score += (len(certs) / 3) * PROFILE_COMPLETION_WEIGHTS['certifications']
    
    # Experience (15%)
    experience_count = profile_data.get('experience_count', 0)
    if experience_count >= 2:
        score += PROFILE_COMPLETION_WEIGHTS['experience']
    elif experience_count > 0:
        score += (experience_count / 2) * PROFILE_COMPLETION_WEIGHTS['experience']
    
    # Achievements (10%)
    achievement_count = profile_data.get('achievement_count', 0)
    if achievement_count >= 3:
        score += PROFILE_COMPLETION_WEIGHTS['achievements']
    elif achievement_count > 0:
        score += (achievement_count / 3) * PROFILE_COMPLETION_WEIGHTS['achievements']
    
    # Social Links (5%)
    social_count = profile_data.get('social_links_count', 0)
    if social_count >= 2:
        score += PROFILE_COMPLETION_WEIGHTS['social_links']
    elif social_count > 0:
        score += (social_count / 2) * PROFILE_COMPLETION_WEIGHTS['social_links']
    
    return min(100, round(score))

def get_earned_badges(profile_data):
    """
    Determine which badges a user has earned based on their profile.
    Returns list of badge codes.
    """
    earned = []
    
    # Profile Complete
    completeness = calculate_profile_completeness(profile_data)
    if completeness == 100:
        earned.append('profile_complete')
    
    # Achievement Hunter
    if profile_data.get('achievement_count', 0) >= 5:
        earned.append('achievement_hunter')
    
    # Veteran
    years_exp = profile_data.get('engineer_profile', {}).get('years_experience', 0)
    try:
        if int(years_exp) >= 20:
            earned.append('veteran')
    except (ValueError, TypeError):
        pass
    
    # Certified Pro
    cert_count = len(profile_data.get('engineer_profile', {}).get('certifications', []))
    if cert_count >= 5:
        earned.append('certified_pro')
    
    # Connected
    if profile_data.get('social_links_count', 0) >= 3:
        earned.append('connected')
    
    return earned


# ═════════════════════════════════════════════════════════════════════════════
# Profile Document Types — Soft-Coded (Unified for Profile + Onboarding)
# ═════════════════════════════════════════════════════════════════════════════
# IMPORTANT: This is the single source of truth for ALL document types across:
#   - User Profile (/profile page)
#   - HR Onboarding (/hr/onboarding page)
# Both features use rbac.ProfileDocument model and share this configuration.
#
# Categories:
#   - 'identity': Personal identity documents (Emirates ID, Passport, etc.)
#   - 'employment': Employment-related documents (Contracts, Offer Letters, etc.)
#   - 'education': Educational and professional certificates
#   - 'compliance': Compliance and legal documents
#   - 'medical': Health and medical records

DOCUMENT_TYPES = {
    # ── Identity Documents (Abu Dhabi UAE Specific) ──────────────────────────
    'emirates_id': {
        'label': 'Emirates ID',
        'code': 'emirates_id',
        'description': 'UAE National Emirates ID Card',
        'icon': '🆔',
        'color': 'blue',
        'category': 'identity',
        'show_in_profile': True,
        'show_in_onboarding': True,
        'required_for_onboarding': False,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf', 'jpg', 'jpeg', 'png'],
        'expiry_tracked': True,
        'verification_required': True,
        'display_order': 1,
        'badge_color': 'bg-blue-500',
        'bg_color': 'bg-blue-50',
        'text_color': 'text-blue-700',
        'border_color': 'border-blue-300',
    },
    'driving_license': {
        'label': 'Driving License',
        'code': 'driving_license',
        'description': 'UAE Driving License',
        'icon': '🚗',
        'color': 'green',
        'category': 'identity',
        'show_in_profile': True,
        'show_in_onboarding': True,
        'required_for_onboarding': False,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf', 'jpg', 'jpeg', 'png'],
        'expiry_tracked': True,
        'verification_required': True,
        'display_order': 2,
        'badge_color': 'bg-green-500',
        'bg_color': 'bg-green-50',
        'text_color': 'text-green-700',
        'border_color': 'border-green-300',
    },
    'country_id': {
        'label': 'Country ID Proof',
        'code': 'country_id',
        'description': 'National ID or Passport from home country',
        'icon': '🌍',
        'color': 'purple',
        'category': 'identity',
        'show_in_profile': True,
        'show_in_onboarding': True,
        'required_for_onboarding': False,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf', 'jpg', 'jpeg', 'png'],
        'expiry_tracked': True,
        'verification_required': True,
        'display_order': 3,
        'badge_color': 'bg-purple-500',
        'bg_color': 'bg-purple-50',
        'text_color': 'text-purple-700',
        'border_color': 'border-purple-300',
    },
    'passport': {
        'label': 'Passport',
        'code': 'passport',
        'description': 'International Passport',
        'icon': '✈️',
        'color': 'indigo',
        'category': 'identity',
        'show_in_profile': True,
        'show_in_onboarding': True,
        'required_for_onboarding': False,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf', 'jpg', 'jpeg', 'png'],
        'expiry_tracked': True,
        'verification_required': True,
        'display_order': 4,
        'badge_color': 'bg-indigo-500',
        'bg_color': 'bg-indigo-50',
        'text_color': 'text-indigo-700',
        'border_color': 'border-indigo-300',
    },
    'visa': {
        'label': 'UAE Visa',
        'code': 'visa',
        'description': 'UAE Residence Visa',
        'icon': '📋',
        'color': 'teal',
        'category': 'identity',
        'show_in_profile': True,
        'show_in_onboarding': True,
        'required_for_onboarding': False,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf', 'jpg', 'jpeg', 'png'],
        'expiry_tracked': True,
        'verification_required': True,
        'display_order': 5,
        'badge_color': 'bg-teal-500',
        'bg_color': 'bg-teal-50',
        'text_color': 'text-teal-700',
        'border_color': 'border-teal-300',
    },
    'health_insurance': {
        'label': 'Health Insurance',
        'code': 'health_insurance',
        'description': 'Health Insurance Card/Policy',
        'icon': '🏥',
        'color': 'pink',
        'category': 'medical',
        'show_in_profile': True,
        'show_in_onboarding': True,
        'required_for_onboarding': False,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf', 'jpg', 'jpeg', 'png'],
        'expiry_tracked': True,
        'verification_required': True,
        'display_order': 6,
        'badge_color': 'bg-pink-500',
        'bg_color': 'bg-pink-50',
        'text_color': 'text-pink-700',
        'border_color': 'border-pink-300',
    },
    
    # ── Employment Documents ──────────────────────────────────────────────────
    'offer_letter': {
        'label': 'Offer Letter',
        'code': 'offer_letter',
        'description': 'Signed Job Offer Letter',
        'icon': '📄',
        'color': 'amber',
        'category': 'employment',
        'show_in_profile': False,
        'show_in_onboarding': True,
        'required_for_onboarding': True,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf'],
        'expiry_tracked': False,
        'verification_required': True,
        'display_order': 10,
        'badge_color': 'bg-amber-500',
        'bg_color': 'bg-amber-50',
        'text_color': 'text-amber-700',
        'border_color': 'border-amber-300',
    },
    'contract': {
        'label': 'Employment Contract',
        'code': 'contract',
        'description': 'Signed Employment Contract',
        'icon': '📜',
        'color': 'orange',
        'category': 'employment',
        'show_in_profile': False,
        'show_in_onboarding': True,
        'required_for_onboarding': True,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf'],
        'expiry_tracked': True,
        'verification_required': True,
        'display_order': 11,
        'badge_color': 'bg-orange-500',
        'bg_color': 'bg-orange-50',
        'text_color': 'text-orange-700',
        'border_color': 'border-orange-300',
    },
    
    # ── Education & Professional Documents ────────────────────────────────────
    'degree': {
        'label': 'Educational Certificates',
        'code': 'degree',
        'description': 'Degree certificates, transcripts, diplomas',
        'icon': '🎓',
        'color': 'violet',
        'category': 'education',
        'show_in_profile': False,
        'show_in_onboarding': True,
        'required_for_onboarding': True,
        'required': False,
        'max_file_size_mb': 10,
        'allowed_formats': ['pdf', 'jpg', 'jpeg', 'png'],
        'expiry_tracked': False,
        'verification_required': True,
        'display_order': 20,
        'badge_color': 'bg-violet-500',
        'bg_color': 'bg-violet-50',
        'text_color': 'text-violet-700',
        'border_color': 'border-violet-300',
    },
    'certificate': {
        'label': 'Professional Certificate',
        'code': 'certificate',
        'description': 'Professional certifications, licenses, accreditations',
        'icon': '📜',
        'color': 'cyan',
        'category': 'education',
        'show_in_profile': False,
        'show_in_onboarding': True,
        'required_for_onboarding': False,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf', 'jpg', 'jpeg', 'png'],
        'expiry_tracked': True,
        'verification_required': True,
        'display_order': 21,
        'badge_color': 'bg-cyan-500',
        'bg_color': 'bg-cyan-50',
        'text_color': 'text-cyan-700',
        'border_color': 'border-cyan-300',
    },
    'experience': {
        'label': 'Experience Letters',
        'code': 'experience',
        'description': 'Employment verification, reference letters',
        'icon': '💼',
        'color': 'lime',
        'category': 'education',
        'show_in_profile': False,
        'show_in_onboarding': True,
        'required_for_onboarding': False,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf'],
        'expiry_tracked': False,
        'verification_required': True,
        'display_order': 22,
        'badge_color': 'bg-lime-500',
        'bg_color': 'bg-lime-50',
        'text_color': 'text-lime-700',
        'border_color': 'border-lime-300',
    },
    
    # ── Compliance & Legal Documents ──────────────────────────────────────────
    'confidentiality': {
        'label': 'Confidentiality Agreement',
        'code': 'confidentiality',
        'description': 'NDA, confidentiality agreement',
        'icon': '🔒',
        'color': 'slate',
        'category': 'compliance',
        'show_in_profile': False,
        'show_in_onboarding': True,
        'required_for_onboarding': True,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf'],
        'expiry_tracked': False,
        'verification_required': True,
        'display_order': 30,
        'badge_color': 'bg-slate-500',
        'bg_color': 'bg-slate-50',
        'text_color': 'text-slate-700',
        'border_color': 'border-slate-300',
    },
    'policy_acknowledgment': {
        'label': 'Policy Acknowledgment',
        'code': 'policy_acknowledgment',
        'description': 'Company policies acknowledgment form',
        'icon': '✓',
        'color': 'emerald',
        'category': 'compliance',
        'show_in_profile': False,
        'show_in_onboarding': True,
        'required_for_onboarding': True,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf'],
        'expiry_tracked': False,
        'verification_required': True,
        'display_order': 31,
        'badge_color': 'bg-emerald-500',
        'bg_color': 'bg-emerald-50',
        'text_color': 'text-emerald-700',
        'border_color': 'border-emerald-300',
    },
    'police_clearance': {
        'label': 'Police Clearance',
        'code': 'police_clearance',
        'description': 'Police clearance certificate, background check',
        'icon': '🛡️',
        'color': 'sky',
        'category': 'compliance',
        'show_in_profile': False,
        'show_in_onboarding': True,
        'required_for_onboarding': False,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf', 'jpg', 'jpeg', 'png'],
        'expiry_tracked': True,
        'verification_required': True,
        'display_order': 32,
        'badge_color': 'bg-sky-500',
        'bg_color': 'bg-sky-50',
        'text_color': 'text-sky-700',
        'border_color': 'border-sky-300',
    },
    
    # ── Other Supporting Documents ────────────────────────────────────────────
    'bank_details': {
        'label': 'Bank Account Details',
        'code': 'bank_details',
        'description': 'Bank account information, IBAN, salary transfer form',
        'icon': '🏦',
        'color': 'rose',
        'category': 'employment',
        'show_in_profile': False,
        'show_in_onboarding': True,
        'required_for_onboarding': True,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf', 'jpg', 'jpeg', 'png'],
        'expiry_tracked': False,
        'verification_required': True,
        'display_order': 40,
        'badge_color': 'bg-rose-500',
        'bg_color': 'bg-rose-50',
        'text_color': 'text-rose-700',
        'border_color': 'border-rose-300',
    },
    'emergency_contact': {
        'label': 'Emergency Contact',
        'code': 'emergency_contact',
        'description': 'Emergency contact information form',
        'icon': '🚨',
        'color': 'red',
        'category': 'employment',
        'show_in_profile': False,
        'show_in_onboarding': True,
        'required_for_onboarding': True,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf'],
        'expiry_tracked': False,
        'verification_required': False,
        'display_order': 41,
        'badge_color': 'bg-red-500',
        'bg_color': 'bg-red-50',
        'text_color': 'text-red-700',
        'border_color': 'border-red-300',
    },
    'medical': {
        'label': 'Medical Forms',
        'code': 'medical',
        'description': 'Medical examination reports, fitness certificates',
        'icon': '🩺',
        'color': 'fuchsia',
        'category': 'medical',
        'show_in_profile': False,
        'show_in_onboarding': True,
        'required_for_onboarding': False,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf', 'jpg', 'jpeg', 'png'],
        'expiry_tracked': True,
        'verification_required': True,
        'display_order': 50,
        'badge_color': 'bg-fuchsia-500',
        'bg_color': 'bg-fuchsia-50',
        'text_color': 'text-fuchsia-700',
        'border_color': 'border-fuchsia-300',
    },
    'vaccination': {
        'label': 'Vaccination Certificate',
        'code': 'vaccination',
        'description': 'COVID-19 vaccination certificate, immunization records',
        'icon': '💉',
        'color': 'yellow',
        'category': 'medical',
        'show_in_profile': False,
        'show_in_onboarding': True,
        'required_for_onboarding': False,
        'required': False,
        'max_file_size_mb': 5,
        'allowed_formats': ['pdf', 'jpg', 'jpeg', 'png'],
        'expiry_tracked': True,
        'verification_required': True,
        'display_order': 51,
        'badge_color': 'bg-yellow-500',
        'bg_color': 'bg-yellow-50',
        'text_color': 'text-yellow-700',
        'border_color': 'border-yellow-300',
    },
    'other': {
        'label': 'Other Documents',
        'code': 'other',
        'description': 'Other supporting documents',
        'icon': '📎',
        'color': 'gray',
        'category': 'other',
        'show_in_profile': True,
        'show_in_onboarding': True,
        'required_for_onboarding': False,
        'required': False,
        'max_file_size_mb': 10,
        'allowed_formats': ['pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx'],
        'expiry_tracked': False,
        'verification_required': False,
        'display_order': 99,
        'badge_color': 'bg-gray-500',
        'bg_color': 'bg-gray-50',
        'text_color': 'text-gray-700',
        'border_color': 'border-gray-300',
    },
}

# Document verification statuses
DOCUMENT_STATUSES = [
    ('pending', 'Pending Review'),
    ('verified', 'Verified'),
    ('rejected', 'Rejected'),
    ('expired', 'Expired'),
]

# Document categories for grouping
DOCUMENT_CATEGORIES = {
    'identity': {
        'label': 'Identity Documents',
        'description': 'Personal identification documents',
        'icon': '🆔',
        'color': 'blue',
    },
    'employment': {
        'label': 'Employment Documents',
        'description': 'Employment contracts, offer letters, bank details',
        'icon': '💼',
        'color': 'orange',
    },
    'education': {
        'label': 'Education & Professional',
        'description': 'Degrees, certifications, experience letters',
        'icon': '🎓',
        'color': 'violet',
    },
    'compliance': {
        'label': 'Compliance & Legal',
        'description': 'Legal agreements, clearances, policies',
        'icon': '🔒',
        'color': 'slate',
    },
    'medical': {
        'label': 'Medical & Health',
        'description': 'Health insurance, medical forms, vaccination records',
        'icon': '🏥',
        'color': 'pink',
    },
    'other': {
        'label': 'Other Documents',
        'description': 'Miscellaneous supporting documents',
        'icon': '📎',
        'color': 'gray',
    },
}


def get_document_type(code):
    """Get document type configuration by code."""
    return DOCUMENT_TYPES.get(code)


def get_all_document_types():
    """Get all document types sorted by display_order."""
    return sorted(
        [
            {
                'code': code,
                **config
            }
            for code, config in DOCUMENT_TYPES.items()
        ],
        key=lambda x: x['display_order']
    )


def get_document_types_for_profile():
    """Get document types shown in user profile page (identity + health)."""
    return sorted(
        [
            {'code': code, **config}
            for code, config in DOCUMENT_TYPES.items()
            if config.get('show_in_profile', False)
        ],
        key=lambda x: x.get('display_order', 999)
    )


def get_document_types_for_onboarding():
    """Get document types shown in HR onboarding page (all employment docs)."""
    return sorted(
        [
            {'code': code, **config}
            for code, config in DOCUMENT_TYPES.items()
            if config.get('show_in_onboarding', False)
        ],
        key=lambda x: x.get('display_order', 999)
    )


def get_document_types_by_category(category):
    """Get document types for a specific category."""
    return sorted(
        [
            {'code': code, **config}
            for code, config in DOCUMENT_TYPES.items()
            if config.get('category') == category
        ],
        key=lambda x: x.get('display_order', 999)
    )


def get_required_onboarding_documents():
    """Get document types required for onboarding completion."""
    return sorted(
        [
            {'code': code, **config}
            for code, config in DOCUMENT_TYPES.items()
            if config.get('required_for_onboarding', False)
        ],
        key=lambda x: x.get('display_order', 999)
    )


# ═════════════════════════════════════════════════════════════════════════════
# Profile Auto-Provisioning Defaults — Soft-Coded
# ═════════════════════════════════════════════════════════════════════════════
# Single source of truth for the fallback UserProfile created on-demand the
# first time a user saves an Achievement / Experience / Social Link / Document
# without an existing profile (see apps.rbac.profile_utils.get_or_create_profile).
# Change these values here only — never hardcode them at call sites.
PROFILE_AUTO_PROVISION = {
    'organization_code': 'default',
    'organization_name': 'Rejlers Engineering',
    'organization_description': 'Default organization (auto-provisioned)',
}

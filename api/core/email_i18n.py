"""Language for outgoing employer emails (English or Arabic).

The site is English and Arabic, so every email to an employer is sent in
the language they use: the locale the request came from when there is one
(the page they were on), otherwise the preferred language saved on their
profile. Team members follow their company's language, and candidates
their own preferred language. Anything else gets English.
"""
from django.utils import timezone

ARABIC = "ar"
ENGLISH = "en"

SIGNATURE = {
    ENGLISH: "Best regards,\nMeritLense Team",
    ARABIC: "مع أطيب التحيات،\nفريق MeritLense",
}

_AR_WEEKDAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
_AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]

EVALUATION_TYPES_AR = {
    "INTERVIEW": "مقابلة",
    "TECHNICAL_TEST": "اختبار تقني",
    "ASSESSMENT": "تقييم",
    "LANGUAGE_PROFICIENCY": "الكفاءة اللغوية",
}

DEAL_TYPES_AR = {
    "STARTER": "الباقة الابتدائية",
    "ENTERPRISE": "باقة المؤسسات",
}

# Mirrors PLAN_NAME_AR in the UI (app/api/payments/types.ts).
PLAN_NAMES_AR = {
    "basic": "الأساسية", "basic package": "الباقة الأساسية",
    "essential": "الضرورية", "essential package": "الباقة الضرورية",
    "advanced": "المتقدمة", "advanced package": "الباقة المتقدمة",
    "premium": "المميزة", "premium package": "الباقة المميزة",
    "growth": "باقة النمو", "growth package": "باقة النمو",
    "business": "باقة الأعمال", "business package": "باقة الأعمال",
    "starter": "الباقة الابتدائية", "enterprise": "باقة المؤسسات",
}

INVOICE_STATUS_AR = {
    "DRAFT": "مسودة", "OPEN": "مستحقة", "PAID": "مدفوعة",
    "UNCOLLECTIBLE": "غير قابلة للتحصيل", "VOID": "ملغاة",
}


def plan_name(name, language):
    if language != ARABIC or not name:
        return name
    return PLAN_NAMES_AR.get(str(name).strip().lower(), name)


TEAM_PERMISSIONS_AR = {
    "add_candidates": "إضافة المرشحين",
    "set_evaluation": "إنشاء التقييمات وإدارتها",
    "set_scores": "تحديد الدرجات",
    "set_payment": "الفواتير والمدفوعات",
    "view_results": "الاطلاع على جميع نتائج الشركة",
}


def _normalise(code):
    code = str(code or "").strip().lower()
    if code.startswith("ar"):
        return ARABIC
    if code.startswith("en"):
        return ENGLISH
    return None


def request_language(request):
    """The site language the request was made from, if it says so."""
    if request is None:
        return None
    for source in (getattr(request, "GET", None), getattr(request, "data", None)):
        try:
            found = _normalise(source.get("locale")) if source is not None else None
        except Exception:
            found = None
        if found:
            return found
    return None


def user_language(user):
    """The preferred language saved on an employer's profile."""
    if user is None:
        return ENGLISH
    for attr in ("company_profile", "individual_profile"):
        profile = getattr(user, attr, None)
        language = _normalise(getattr(profile, "preferred_language", None))
        if language:
            return language
    member = getattr(user, "team_member_profile", None)
    owner = getattr(getattr(member, "company", None), "admin_user", None)
    if owner is not None and owner != user:
        return user_language(owner)
    return ENGLISH


def email_language(user=None, request=None):
    return request_language(request) or user_language(user)


def company_language(company):
    owner = getattr(company, "admin_user", None)
    return user_language(owner) if owner is not None else ENGLISH


def candidate_language(candidate):
    return _normalise(getattr(candidate, "preferred_language", None)) or ENGLISH


def format_date(value, language, *, with_weekday=True):
    """A date in the recipient's language, already in the display zone."""
    if language != ARABIC:
        return value.strftime("%A, %B %d, %Y" if with_weekday else "%B %d, %Y")
    text = f"{value.day} {_AR_MONTHS[value.month - 1]} {value.year}"
    return f"{_AR_WEEKDAYS[value.weekday()]}، {text}" if with_weekday else text


def format_time(value, language):
    if language != ARABIC:
        return value.strftime("%I:%M %p")
    suffix = "ص" if value.hour < 12 else "م"
    return f"{value.strftime('%I:%M')} {suffix}"


def site_url_locale(language):
    return ARABIC if language == ARABIC else ENGLISH


def now_language_date(language):
    return format_date(timezone.localtime(), language, with_weekday=False)

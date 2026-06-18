class Roles:
    SUPERADMIN = "SUPERADMIN"
    ADMIN = "ADMIN"
    B2B = "B2B"
    B2C = "B2C"
    B2B_TEAM_MEMBER = "B2B_TEAM_MEMBER"
    CANDIDATE = "CANDIDATE"

    CHOICES = [
        (SUPERADMIN, "Super Admin"),
        (ADMIN, "Admin"),
        (B2B, "B2B Company"),
        (B2C, "B2C Individual"),
        (B2B_TEAM_MEMBER, "B2B Team Member"),
        (CANDIDATE, "Candidate"),
    ]


class InterviewSessionStatus:
    CREATED = "CREATED"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

    CHOICES = [
        (CREATED, "Created"),
        (VERIFICATION_PENDING, "Verification Pending"),
        (READY, "Ready"),
        (IN_PROGRESS, "In Progress"),
        (PAUSED, "Paused"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
        (EXPIRED, "Expired"),
        (CANCELLED, "Cancelled"),
    ]


class SessionQuestionStatus:
    PENDING = "PENDING"
    ASKED = "ASKED"
    ANSWERED = "ANSWERED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"

    CHOICES = [
        (PENDING, "Pending"),
        (ASKED, "Asked"),
        (ANSWERED, "Answered"),
        (SKIPPED, "Skipped"),
        (FAILED, "Failed"),
    ]


class CandidateResponseType:
    TEXT = "TEXT"
    VOICE = "VOICE"
    TASK = "TASK"

    CHOICES = [
        (TEXT, "Text"),
        (VOICE, "Voice"),
        (TASK, "Task"),
    ]


class InterviewEvaluationTier:
    FULL = "FULL"
    SCREENING = "SCREENING"
    BOTH = "BOTH"

    CHOICES = [
        (FULL, "Full Evaluation"),
        (SCREENING, "Screening"),
        (BOTH, "Both"),
    ]


class InterviewQuestionType:
    SAFETY = "safety"
    INTEGRITY = "integrity"
    BEHAVIORAL = "behavioral"
    COMMUNICATION = "communication"
    SITUATIONAL = "situational"
    SCENARIO = "scenario"
    KNOWLEDGE = "knowledge"

    CHOICES = [
        (SAFETY, "Safety"),
        (INTEGRITY, "Integrity"),
        (BEHAVIORAL, "Behavioral"),
        (COMMUNICATION, "Communication"),
        (SITUATIONAL, "Situational"),
        (SCENARIO, "Scenario"),
        (KNOWLEDGE, "Knowledge"),
    ]


class InterviewQuestionFormat:
    TEXT = "TEXT"
    SCENARIO = "SCENARIO"
    TASK = "TASK"

    CHOICES = [
        (TEXT, "Text"),
        (SCENARIO, "Scenario"),
        (TASK, "Task"),
    ]


class QuestionLifecycleStatus:
    ACTIVE = "active"
    DRAFT = "draft"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"

    CHOICES = [
        (ACTIVE, "Active"),
        (DRAFT, "Draft"),
        (DEPRECATED, "Deprecated"),
        (ARCHIVED, "Archived"),
    ]


class ExpectedAnswerType:
    SHORT = "short"
    STRUCTURED = "structured"
    MULTI_STEP = "multi_step"

    CHOICES = [
        (SHORT, "Short"),
        (STRUCTURED, "Structured"),
        (MULTI_STEP, "Multi Step"),
    ]


class QuestionDifficulty:
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"

    CHOICES = [
        (EASY, "Easy"),
        (MEDIUM, "Medium"),
        (HARD, "Hard"),
    ]


class IdentityVerificationStatus:
    NOT_STARTED = "NOT_STARTED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"

    CHOICES = [
        (NOT_STARTED, "Not Started"),
        (PENDING, "Pending"),
        (VERIFIED, "Verified"),
        (FAILED, "Failed"),
    ]


class ReadinessStatus:
    PENDING = "PENDING"
    READY = "READY"
    NOT_READY = "NOT_READY"

    CHOICES = [
        (PENDING, "Pending"),
        (READY, "Ready"),
        (NOT_READY, "Not Ready"),
    ]


class AdminPermissions:
    USER_MANAGEMENT = "can_manage_users"
    COMPANY_VERIFICATION = "can_verify_companies"
    DOCUMENT_VERIFICATION = "can_verify_documents"
    FINANCIAL_ACCESS = "can_access_financial"
    REPORTS_ACCESS = "can_access_reports"
    
    CHOICES = [
        (USER_MANAGEMENT, "User Management"),
        (COMPANY_VERIFICATION, "Company Verification"),
        (DOCUMENT_VERIFICATION, "Document Verification"),
        (FINANCIAL_ACCESS, "Financial Access"),
        (REPORTS_ACCESS, "Reports Access"),
    ]


class DocumentStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    
    CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
    ]


class CompanyTeamPermissions:
    VIEW_CANDIDATES = "view_candidates"
    EVALUATE_CANDIDATES = "evaluate_candidates"
    CREATE_EVALUATIONS = "create_evaluations"
    VIEW_REPORTS = "view_reports"
    
    CHOICES = [
        (VIEW_CANDIDATES, "View Candidates"),
        (EVALUATE_CANDIDATES, "Evaluate Candidates"),
        (CREATE_EVALUATIONS, "Create Evaluations"),
        (VIEW_REPORTS, "View Reports"),
    ]

class Languages:
    ENGLISH = "EN"
    SPANISH = "ES"
    FRENCH = "FR"
    ARABIC = "AR"
    GERMAN = "DE"
    CHINESE = "ZH"

    CHOICES = [
        (ENGLISH, "English"),
        (SPANISH, "Spanish"),
        (FRENCH, "French"),
        (ARABIC, "Arabic"),
        (GERMAN, "German"),
        (CHINESE, "Chinese"),
    ]


class JobRoles:
    SOFTWARE_ENGINEER = "SE"
    DATA_SCIENTIST = "DS"
    PRODUCT_MANAGER = "PM"
    MARKETING = "MK"
    SALES = "SL"
    HR = "HR"
    FINANCE = "FN"
    OTHER = "OT"

    CHOICES = [
        (SOFTWARE_ENGINEER, "Software Engineer"),
        (DATA_SCIENTIST, "Data Scientist"),
        (PRODUCT_MANAGER, "Product Manager"),
        (MARKETING, "Marketing"),
        (SALES, "Sales"),
        (HR, "Human Resources"),
        (FINANCE, "Finance"),
        (OTHER, "Other"),
    ]
    
class candidateJobRoles:
    NANNY = 'NA'
    DRIVER = 'DR'
    HOUSEKEEPER = 'HK'
    ELDERCOMPANION = 'EC'
    KITCHENASSISTANT = 'KA'
    MAINTAINANCEWORKER = 'MW'
    OTHER = 'OT'
    
    CHOICES = [
        (NANNY, 'Nanny'),
        (DRIVER, 'Driver'),
        (HOUSEKEEPER, 'Housekeeper'),
        (ELDERCOMPANION, 'Elder Companion'),
        (KITCHENASSISTANT, 'Kitchen Assistant'),
        (MAINTAINANCEWORKER, 'Maintenance Worker'),
        (OTHER, 'Other'),
    ]


class Nationalities:
    US = "US"
    UK = "GB"
    CANADA = "CA"
    AUSTRALIA = "AU"
    GERMANY = "DE"
    FRANCE = "FR"
    SPAIN = "ES"
    ITALY = "IT"
    UAE = "AE"
    SAUDI = "SA"
    EGYPT = "EG"
    INDIA = "IN"
    CHINA = "CN"
    JAPAN = "JP"
    BRAZIL = "BR"
    MEXICO = "MX"
    OTHER = "OT"

    CHOICES = [
        (US, "United States"),
        (UK, "United Kingdom"),
        (CANADA, "Canada"),
        (AUSTRALIA, "Australia"),
        (GERMANY, "Germany"),
        (FRANCE, "France"),
        (SPAIN, "Spain"),
        (ITALY, "Italy"),
        (UAE, "UAE"),
        (SAUDI, "Saudi Arabia"),
        (EGYPT, "Egypt"),
        (INDIA, "India"),
        (CHINA, "China"),
        (JAPAN, "Japan"),
        (BRAZIL, "Brazil"),
        (MEXICO, "Mexico"),
        (OTHER, "Other"),
    ]


class CompanySize:
    STARTUP = "1-10"
    SMALL = "11-50"
    MEDIUM = "51-200"
    LARGE = "201-1000"
    ENTERPRISE = "1000+"

    CHOICES = [
        (STARTUP, "1-10 employees"),
        (SMALL, "11-50 employees"),
        (MEDIUM, "51-200 employees"),
        (LARGE, "201-1000 employees"),
        (ENTERPRISE, "1000+ employees"),
    ]

class CandidateStatus:
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    HIRED = "HIRED"
    REJECTED = "REJECTED"
    
    CHOICES = [
        (ACTIVE, "Active"),
        (ARCHIVED, "Archived"),
        (HIRED, "Hired"),
        (REJECTED, "Rejected"),
    ]

class EvaluationType:
    INTERVIEW = "INTERVIEW"
    TECHNICAL_TEST = "TECHNICAL_TEST"
    ASSESSMENT = "ASSESSMENT"
    LANGUAGE_PROFICIENCY = "LANGUAGE_PROFICIENCY"
    
    CHOICES = [
        (INTERVIEW, "Interview"),
        (TECHNICAL_TEST, "Technical Test"),
        (ASSESSMENT, "Assessment"),
        (LANGUAGE_PROFICIENCY, "Language Proficiency"),
    ]


class EvaluationStatus:
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    RESCHEDULED = "RESCHEDULED"
    
    CHOICES = [
        (SCHEDULED, "Scheduled"),
        (IN_PROGRESS, "In Progress"),
        (COMPLETED, "Completed"),
        (CANCELLED, "Cancelled"),
        (RESCHEDULED, "Rescheduled"),
    ]


class CertificateStatus:
    NOT_ISSUED = "NOT_ISSUED"
    PENDING = "PENDING"
    ISSUED = "ISSUED"
    EXPIRED = "EXPIRED"
    
    CHOICES = [
        (NOT_ISSUED, "Not Issued"),
        (PENDING, "Pending"),
        (ISSUED, "Issued"),
        (EXPIRED, "Expired"),
    ]


class ScoreArea:
    
    COMMUNICATION = "COMMUNICATION"
    RELIABILITY = "RELIABILITY"
    PUNCTUALITY = "PUNCTUALITY"
    PROFESSIONALISM = "PROFESSIONALISM"
    TEAMWORK = "TEAMWORK"
    
    CLEANING = "CLEANING"
    MAINTENANCE = "MAINTENANCE"
    ORGANIZATION = "ORGANIZATION"
    ATTENTION_TO_DETAIL = "ATTENTION_TO_DETAIL"
    
    COMPANIONSHIP = "COMPANIONSHIP"
    PATIENCE = "PATIENCE"
    MEDICATION_MANAGEMENT = "MEDICATION_MANAGEMENT"
    FIRST_AID = "FIRST_AID"
    ELDERLY_CARE = "ELDERLY_CARE"
    CHILD_CARE = "CHILD_CARE"
    
    SAFE_DRIVING = "SAFE_DRIVING"
    ROUTE_PLANNING = "ROUTE_PLANNING"
    VEHICLE_MAINTENANCE = "VEHICLE_MAINTENANCE"
    NAVIGATION = "NAVIGATION"
    
    FOOD_PREPARATION = "FOOD_PREPARATION"
    KITCHEN_HYGIENE = "KITCHEN_HYGIENE"
    MENU_PLANNING = "MENU_PLANNING"
    BUDGETING = "BUDGETING"
    DIETARY_KNOWLEDGE = "DIETARY_KNOWLEDGE"
    
    CHOICES = [
        (COMMUNICATION, "Communication"),
        (RELIABILITY, "Reliability"),
        (PUNCTUALITY, "Punctuality"),
        (PROFESSIONALISM, "Professionalism"),
        (TEAMWORK, "Teamwork"),
        
        (CLEANING, "Cleaning"),
        (MAINTENANCE, "Maintenance"),
        (ORGANIZATION, "Organization"),
        (ATTENTION_TO_DETAIL, "Attention to Detail"),
        
        (COMPANIONSHIP, "Companionship"),
        (PATIENCE, "Patience"),
        (MEDICATION_MANAGEMENT, "Medication Management"),
        (FIRST_AID, "First Aid"),
        (ELDERLY_CARE, "Elderly Care"),
        (CHILD_CARE, "Child Care"),
        
        (SAFE_DRIVING, "Safe Driving"),
        (ROUTE_PLANNING, "Route Planning"),
        (VEHICLE_MAINTENANCE, "Vehicle Maintenance"),
        (NAVIGATION, "Navigation"),
        
        (FOOD_PREPARATION, "Food Preparation"),
        (KITCHEN_HYGIENE, "Kitchen Hygiene"),
        (MENU_PLANNING, "Menu Planning"),
        (BUDGETING, "Budgeting"),
        (DIETARY_KNOWLEDGE, "Dietary Knowledge"),
        
    ]


JOB_ROLE_SCORE_AREAS = {
    "HK": [
        ScoreArea.CLEANING,
        ScoreArea.MAINTENANCE,
        ScoreArea.ORGANIZATION,
        ScoreArea.ATTENTION_TO_DETAIL,
        ScoreArea.RELIABILITY,
        ScoreArea.PUNCTUALITY,
        ScoreArea.COMMUNICATION,
    ],
    
    "EC": [
        ScoreArea.COMPANIONSHIP,
        ScoreArea.PATIENCE,
        ScoreArea.MEDICATION_MANAGEMENT,
        ScoreArea.FIRST_AID,
        ScoreArea.ELDERLY_CARE,
        ScoreArea.COMMUNICATION,
        ScoreArea.RELIABILITY,
    ],
    "NA": [
        ScoreArea.FIRST_AID,
        ScoreArea.MEDICATION_MANAGEMENT,
        ScoreArea.ELDERLY_CARE,
        ScoreArea.PATIENCE,
        ScoreArea.COMMUNICATION,
        ScoreArea.PROFESSIONALISM,
        ScoreArea.TEAMWORK,
    ],
    
    "DR": [
        ScoreArea.SAFE_DRIVING,
        ScoreArea.ROUTE_PLANNING,
        ScoreArea.VEHICLE_MAINTENANCE,
        ScoreArea.NAVIGATION,
        ScoreArea.PUNCTUALITY,
        ScoreArea.RELIABILITY,
        ScoreArea.PROFESSIONALISM,
    ],
    
    "KA": [
        ScoreArea.FOOD_PREPARATION,
        ScoreArea.KITCHEN_HYGIENE,
        ScoreArea.MENU_PLANNING,
        ScoreArea.CLEANING,
        ScoreArea.BUDGETING,
        ScoreArea.DIETARY_KNOWLEDGE,
        ScoreArea.ORGANIZATION,
        ScoreArea.TEAMWORK,
    ],
    
    "MW": [
        ScoreArea.MAINTENANCE,
        ScoreArea.ORGANIZATION,
        ScoreArea.ATTENTION_TO_DETAIL,
        ScoreArea.RELIABILITY,
        ScoreArea.PUNCTUALITY,
        ScoreArea.COMMUNICATION,
    ],
    
    "OT": [
        ScoreArea.COMMUNICATION,
        ScoreArea.RELIABILITY,
        ScoreArea.PUNCTUALITY,
        ScoreArea.PROFESSIONALISM,
        ScoreArea.TEAMWORK,
    ],
}

class PaymentMethodConstants:
    CARD = "CARD"
    BANK_TRANSFER = "BANK_TRANSFER"
    
    CHOICES = [
        (CARD, "Credit/Debit Card"),
        (BANK_TRANSFER, "Bank Transfer"),
    ]

class PaymentStatus:
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"
    
    CHOICES = [
        (PENDING, "Pending"),
        (SUCCEEDED, "Succeeded"),
        (FAILED, "Failed"),
        (REFUNDED, "Refunded"),
        (CANCELLED, "Cancelled"),
    ]

class SubscriptionStatus:
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELED = "CANCELED"
    INCOMPLETE = "INCOMPLETE"
    INCOMPLETE_EXPIRED = "INCOMPLETE_EXPIRED"
    TRIALING = "TRIALING"
    UNPAID = "UNPAID"
    
    CHOICES = [
        (ACTIVE, "Active"),
        (PAST_DUE, "Past Due"),
        (CANCELED, "Canceled"),
        (INCOMPLETE, "Incomplete"),
        (INCOMPLETE_EXPIRED, "Incomplete Expired"),
        (TRIALING, "Trialing"),
        (UNPAID, "Unpaid"),
    ]


class BillingInterval:
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    YEARLY = "YEARLY"
    
    CHOICES = [
        (MONTHLY, "Monthly"),
        (QUARTERLY, "Quarterly"),
        (YEARLY, "Yearly"),
    ]


class InvoiceStatus:
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    PAID = "PAID"
    VOID = "VOID"
    UNCOLLECTIBLE = "UNCOLLECTIBLE"
    
    CHOICES = [
        (DRAFT, "Draft"),
        (OPEN, "Open"),
        (PAID, "Paid"),
        (VOID, "Void"),
        (UNCOLLECTIBLE, "Uncollectible"),
    ]
    

class AuditLogCategory:
    USER = "USER"
    CANDIDATE = "CANDIDATE"
    SESSION = "SESSION"
    QUESTION = "QUESTION"
    INTEGRITY = "INTEGRITY"
    EVALUATION = "EVALUATION"
    SUBSCRIPTION = "SUBSCRIPTION"
    PAYMENT = "PAYMENT"
    DOCUMENT = "DOCUMENT"
    SECURITY = "SECURITY"
    COMPANY = "COMPANY"
    TEAM = "TEAM"
    BILLING = "BILLING"
    SYSTEM = "SYSTEM"
    
    CHOICES = [
        (USER, "User Management"),
        (CANDIDATE, "Candidate Management"),
        (SESSION, "Interview Session"),
        (QUESTION, "Interview Question"),
        (INTEGRITY, "Integrity Monitoring"),
        (EVALUATION, "Evaluation"),
        (SUBSCRIPTION, "Subscription"),
        (PAYMENT, "Payment"),
        (DOCUMENT, "Document"),
        (SECURITY, "Security"),
        (COMPANY, "Company"),
        (TEAM, "Team"),
        (BILLING, "Billing"),
        (SYSTEM, "System"),
    ]


class AuditLogAction:
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    USER_DELETED = "USER_DELETED"
    USER_VERIFIED = "USER_VERIFIED"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"
    PASSWORD_RESET = "PASSWORD_RESET"
    
    CANDIDATE_CREATED = "CANDIDATE_CREATED"
    CANDIDATE_UPDATED = "CANDIDATE_UPDATED"
    CANDIDATE_DELETED = "CANDIDATE_DELETED"
    CANDIDATE_SHARED = "CANDIDATE_SHARED"
    
    VIEW_MY_CANDIDATES = "VIEW_MY_CANDIDATES"
    VIEW_SHARED_CANDIDATES = "VIEW_SHARED_CANDIDATES"
    VIEW_CANDIDATE = "VIEW_CANDIDATE"
    VIEW_CANDIDATES_LIST = "VIEW_CANDIDATES_LIST"

    SESSION_CREATED = "SESSION_CREATED"
    SESSION_VERIFICATION_PENDING = "SESSION_VERIFICATION_PENDING"
    SESSION_READY = "SESSION_READY"
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_PAUSED = "SESSION_PAUSED"
    SESSION_COMPLETED = "SESSION_COMPLETED"
    SESSION_FAILED = "SESSION_FAILED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    SESSION_CANCELLED = "SESSION_CANCELLED"
    QUESTION_SENT = "QUESTION_SENT"
    QUESTION_VIEWED = "QUESTION_VIEWED"
    ANSWER_SUBMITTED = "ANSWER_SUBMITTED"

    
    EVALUATION_CREATED = "EVALUATION_CREATED"
    EVALUATION_UPDATED = "EVALUATION_UPDATED"
    EVALUATION_COMPLETED = "EVALUATION_COMPLETED"
    EVALUATION_CANCELLED = "EVALUATION_CANCELLED"
    EVALUATION_RESCHEDULED = "EVALUATION_RESCHEDULED"
    EVALUATION_DELETED = "EVALUATION_DELETED"
    VIEW_EVALUATION = "VIEW_EVALUATION"
    VIEW_EVALUATIONS_LIST = "VIEW_EVALUATIONS_LIST"
    VIEW_UPCOMING_EVALUATIONS = "VIEW_UPCOMING_EVALUATIONS"
    VIEW_PAST_EVALUATIONS = "VIEW_PAST_EVALUATIONS"
    VIEW_MY_EVALUATIONS = "VIEW_MY_EVALUATIONS"
    VIEW_CANDIDATE_EVALUATIONS = "VIEW_CANDIDATE_EVALUATIONS"
    
    SCORE_CATEGORY_CREATED = "SCORE_CATEGORY_CREATED"
    SCORE_CATEGORY_UPDATED = "SCORE_CATEGORY_UPDATED"
    SCORE_CATEGORY_DELETED = "SCORE_CATEGORY_DELETED"

    SCORE_SET_CREATED = "SCORE_SET_CREATED"
    SCORE_SET_UPDATED = "SCORE_SET_UPDATED"
    SCORE_SET_DELETED = "SCORE_SET_DELETED"
    SCORE_SET_RECALCULATED = "SCORE_SET_RECALCULATED"
    VIEW_SCORE_SET = "VIEW_SCORE_SET"
    VIEW_SCORE_SETS_LIST = "VIEW_SCORE_SETS_LIST"
    VIEW_SCORES_IN_SET = "VIEW_SCORES_IN_SET"

    INDIVIDUAL_SCORE_CREATED = "INDIVIDUAL_SCORE_CREATED"
    INDIVIDUAL_SCORE_UPDATED = "INDIVIDUAL_SCORE_UPDATED"
    INDIVIDUAL_SCORE_DELETED = "INDIVIDUAL_SCORE_DELETED"
    VIEW_INDIVIDUAL_SCORE = "VIEW_INDIVIDUAL_SCORE"
    VIEW_INDIVIDUAL_SCORES_LIST = "VIEW_INDIVIDUAL_SCORES_LIST"

    VIEW_JOB_ROLE_AREAS = "VIEW_JOB_ROLE_AREAS"
    VIEW_JOB_ROLE_AREAS_BY_ROLE = "VIEW_JOB_ROLE_AREAS_BY_ROLE"
    
    DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
    DOCUMENT_VERIFIED = "DOCUMENT_VERIFIED"
    DOCUMENT_REJECTED = "DOCUMENT_REJECTED"
    
    SUBSCRIPTION_CREATED = "SUBSCRIPTION_CREATED"
    SUBSCRIPTION_UPDATED = "SUBSCRIPTION_UPDATED"
    SUBSCRIPTION_CANCELLED = "SUBSCRIPTION_CANCELLED"
    SUBSCRIPTION_REACTIVATED = "SUBSCRIPTION_REACTIVATED"
    
    PAYMENT_SUCCEEDED = "PAYMENT_SUCCEEDED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_REFUNDED = "PAYMENT_REFUNDED"
    VIEW_PRICES_LIST = "VIEW_PRICES_LIST"
    VIEW_PRICES_FOR_USER = "VIEW_PRICES_FOR_USER"
    SYNC_PRICES_FROM_STRIPE = "SYNC_PRICES_FROM_STRIPE"

    CUSTOMER_RETRIEVED = "CUSTOMER_RETRIEVED"

    VIEW_PAYMENT_METHODS = "VIEW_PAYMENT_METHODS"
    CREATE_SETUP_INTENT = "CREATE_SETUP_INTENT"
    ATTACH_PAYMENT_METHOD = "ATTACH_PAYMENT_METHOD"
    DETACH_PAYMENT_METHOD = "DETACH_PAYMENT_METHOD"
    SET_DEFAULT_PAYMENT_METHOD = "SET_DEFAULT_PAYMENT_METHOD"

    VIEW_SUBSCRIPTIONS_LIST = "VIEW_SUBSCRIPTIONS_LIST"
    VIEW_SUBSCRIPTION_DETAILS = "VIEW_SUBSCRIPTION_DETAILS"
    SUBSCRIPTION_CREATION_FAILED = "SUBSCRIPTION_CREATION_FAILED"
    SUBSCRIPTION_PLAN_CHANGED = "SUBSCRIPTION_PLAN_CHANGED"
    SUBSCRIPTION_QUANTITY_UPDATED = "SUBSCRIPTION_QUANTITY_UPDATED"
    SUBSCRIPTION_REACTIVATED = "SUBSCRIPTION_REACTIVATED"
    VIEW_UPCOMING_INVOICE = "VIEW_UPCOMING_INVOICE"
    VIEW_SUBSCRIPTION_USAGE = "VIEW_SUBSCRIPTION_USAGE"
    VIEW_ACTIVE_SUBSCRIPTIONS = "VIEW_ACTIVE_SUBSCRIPTIONS"
    VIEW_SUBSCRIPTION_INVOICES = "VIEW_SUBSCRIPTION_INVOICES"

    ADMIN_VIEW_ALL_SUBSCRIPTIONS = "ADMIN_VIEW_ALL_SUBSCRIPTIONS"
    ADMIN_VIEW_SUBSCRIPTION_DETAILS = "ADMIN_VIEW_SUBSCRIPTION_DETAILS"
    ADMIN_VIEW_SUBSCRIPTION_STATS = "ADMIN_VIEW_SUBSCRIPTION_STATS"

    VIEW_PAYMENTS_LIST = "VIEW_PAYMENTS_LIST"
    VIEW_PAYMENT_DETAILS = "VIEW_PAYMENT_DETAILS"
    CREATE_PAYMENT_INTENT = "CREATE_PAYMENT_INTENT"

    VIEW_INVOICES_LIST = "VIEW_INVOICES_LIST"
    VIEW_INVOICE_DETAILS = "VIEW_INVOICE_DETAILS"

    STRIPE_WEBHOOK_RECEIVED = "STRIPE_WEBHOOK_RECEIVED"
    STRIPE_WEBHOOK_ERROR = "STRIPE_WEBHOOK_ERROR"

    DEBUG_SUBSCRIPTION_DATA = "DEBUG_SUBSCRIPTION_DATA"
    DEBUG_PAYMENT_DATA = "DEBUG_PAYMENT_DATA"
    RETRY_SUBSCRIPTION_PAYMENT = "RETRY_SUBSCRIPTION_PAYMENT"
    SYNC_SUBSCRIPTION_STATUS = "SYNC_SUBSCRIPTION_STATUS"
    CHECK_ALL_SUBSCRIPTIONS = "CHECK_ALL_SUBSCRIPTIONS"
    SYNC_ALL_SUBSCRIPTIONS = "SYNC_ALL_SUBSCRIPTIONS"
    
    TWO_FACTOR_ENABLED = "TWO_FACTOR_ENABLED"
    TWO_FACTOR_DISABLED = "TWO_FACTOR_DISABLED"
    LOGIN_FAILED = "LOGIN_FAILED"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    
    COMPANY_CREATED = "COMPANY_CREATED"
    COMPANY_UPDATED = "COMPANY_UPDATED"
    COMPANY_VERIFIED = "COMPANY_VERIFIED"
    
    TEAM_MEMBER_INVITED = "TEAM_MEMBER_INVITED"
    TEAM_MEMBER_JOINED = "TEAM_MEMBER_JOINED"
    TEAM_MEMBER_REMOVED = "TEAM_MEMBER_REMOVED"
    TEAM_MEMBER_UPDATED = "TEAM_MEMBER_UPDATED"
    
    INVOICE_GENERATED = "INVOICE_GENERATED"
    PAYMENT_METHOD_ADDED = "PAYMENT_METHOD_ADDED"
    PAYMENT_METHOD_REMOVED = "PAYMENT_METHOD_REMOVED"
    
    SYSTEM_CONFIG_CHANGED = "SYSTEM_CONFIG_CHANGED"
    API_KEY_CREATED = "API_KEY_CREATED"
    API_KEY_REVOKED = "API_KEY_REVOKED"
    
    CHOICES = [
        (USER_CREATED, "User Created"),
        (USER_UPDATED, "User Updated"),
        (USER_DELETED, "User Deleted"),
        (USER_VERIFIED, "User Verified"),
        (PASSWORD_CHANGE, "Password Changed"),
        (PASSWORD_RESET, "Password Reset"),
        
        (CANDIDATE_CREATED, "Candidate Created"),
        (CANDIDATE_UPDATED, "Candidate Updated"),
        (CANDIDATE_DELETED, "Candidate Deleted"),
        (CANDIDATE_SHARED, "Candidate Shared"),
        
        (VIEW_MY_CANDIDATES, "Viewed My Candidates"),
        (VIEW_SHARED_CANDIDATES, "Viewed Shared Candidates"),
        (VIEW_CANDIDATE, "Viewed Candidate Details"),
        (VIEW_CANDIDATES_LIST, "Viewed Candidates List"),

        (SESSION_CREATED, "Session Created"),
        (SESSION_VERIFICATION_PENDING, "Session Verification Pending"),
        (SESSION_READY, "Session Ready"),
        (SESSION_STARTED, "Session Started"),
        (SESSION_PAUSED, "Session Paused"),
        (SESSION_COMPLETED, "Session Completed"),
        (SESSION_FAILED, "Session Failed"),
        (SESSION_EXPIRED, "Session Expired"),
        (SESSION_CANCELLED, "Session Cancelled"),
        (QUESTION_SENT, "Question Sent"),
        (QUESTION_VIEWED, "Question Viewed"),
        (ANSWER_SUBMITTED, "Answer Submitted"),
        
        (EVALUATION_CREATED, "Evaluation Created"),
        (EVALUATION_UPDATED, "Evaluation Updated"),
        (EVALUATION_COMPLETED, "Evaluation Completed"),
        (EVALUATION_CANCELLED, "Evaluation Cancelled"),
        (EVALUATION_RESCHEDULED, "Evaluation Rescheduled"),
        (EVALUATION_DELETED, "Evaluation Deleted"),
        (VIEW_EVALUATION, "Viewed Evaluation Details"),
        (VIEW_EVALUATIONS_LIST, "Viewed Evaluations List"),
        (VIEW_UPCOMING_EVALUATIONS, "Viewed Upcoming Evaluations"),
        (VIEW_PAST_EVALUATIONS, "Viewed Past Evaluations"),
        (VIEW_MY_EVALUATIONS, "Viewed My Evaluations"),
        (VIEW_CANDIDATE_EVALUATIONS, "Viewed Candidate Evaluations"),
        
        (SCORE_CATEGORY_CREATED, "Score Category Created"),
        (SCORE_CATEGORY_UPDATED, "Score Category Updated"),
        (SCORE_CATEGORY_DELETED, "Score Category Deleted"),
        (SCORE_SET_CREATED, "Score Set Created"),
        (SCORE_SET_UPDATED, "Score Set Updated"),
        (SCORE_SET_DELETED, "Score Set Deleted"),
        (SCORE_SET_RECALCULATED, "Score Set Recalculated"),
        (VIEW_SCORE_SET, "Viewed Score Set"),
        (VIEW_SCORE_SETS_LIST, "Viewed Score Sets List"),
        (VIEW_SCORES_IN_SET, "Viewed Scores in Set"),
        (INDIVIDUAL_SCORE_CREATED, "Individual Score Created"),
        (INDIVIDUAL_SCORE_UPDATED, "Individual Score Updated"),
        (INDIVIDUAL_SCORE_DELETED, "Individual Score Deleted"),
        (VIEW_INDIVIDUAL_SCORE, "Viewed Individual Score"),
        (VIEW_INDIVIDUAL_SCORES_LIST, "Viewed Individual Scores List"),
        (VIEW_JOB_ROLE_AREAS, "Viewed Job Role Areas"),
        (VIEW_JOB_ROLE_AREAS_BY_ROLE, "Viewed Job Role Areas By Role"),
        
        (DOCUMENT_UPLOADED, "Document Uploaded"),
        (DOCUMENT_VERIFIED, "Document Verified"),
        (DOCUMENT_REJECTED, "Document Rejected"),
        
        (SUBSCRIPTION_CREATED, "Subscription Created"),
        (SUBSCRIPTION_UPDATED, "Subscription Updated"),
        (SUBSCRIPTION_CANCELLED, "Subscription Cancelled"),
        (SUBSCRIPTION_REACTIVATED, "Subscription Reactivated"),
        
        (PAYMENT_SUCCEEDED, "Payment Succeeded"),
        (PAYMENT_FAILED, "Payment Failed"),
        (PAYMENT_REFUNDED, "Payment Refunded"),
        (VIEW_PRICES_LIST, "Viewed Prices List"),
        (VIEW_PRICES_FOR_USER, "Viewed Filtered Prices"),
        (SYNC_PRICES_FROM_STRIPE, "Synced Prices from Stripe"),
        (CUSTOMER_RETRIEVED, "Customer Retrieved/Created"),
        (VIEW_PAYMENT_METHODS, "Viewed Payment Methods"),
        (CREATE_SETUP_INTENT, "Created Setup Intent"),
        (ATTACH_PAYMENT_METHOD, "Attached Payment Method"),
        (DETACH_PAYMENT_METHOD, "Detached Payment Method"),
        (SET_DEFAULT_PAYMENT_METHOD, "Set Default Payment Method"),
        (VIEW_SUBSCRIPTIONS_LIST, "Viewed Subscriptions List"),
        (VIEW_SUBSCRIPTION_DETAILS, "Viewed Subscription Details"),
        (SUBSCRIPTION_CREATION_FAILED, "Subscription Creation Failed"),
        (SUBSCRIPTION_PLAN_CHANGED, "Subscription Plan Changed"),
        (SUBSCRIPTION_QUANTITY_UPDATED, "Subscription Quantity Updated"),
        (SUBSCRIPTION_REACTIVATED, "Subscription Reactivated"),
        (VIEW_UPCOMING_INVOICE, "Viewed Upcoming Invoice"),
        (VIEW_SUBSCRIPTION_USAGE, "Viewed Subscription Usage"),
        (VIEW_ACTIVE_SUBSCRIPTIONS, "Viewed Active Subscriptions"),
        (VIEW_SUBSCRIPTION_INVOICES, "Viewed Subscription Invoices"),
        (ADMIN_VIEW_ALL_SUBSCRIPTIONS, "Admin Viewed All Subscriptions"),
        (ADMIN_VIEW_SUBSCRIPTION_DETAILS, "Admin Viewed Subscription Details"),
        (ADMIN_VIEW_SUBSCRIPTION_STATS, "Admin Viewed Subscription Stats"),
        (VIEW_PAYMENTS_LIST, "Viewed Payments List"),
        (VIEW_PAYMENT_DETAILS, "Viewed Payment Details"),
        (CREATE_PAYMENT_INTENT, "Created Payment Intent"),
        (VIEW_INVOICES_LIST, "Viewed Invoices List"),
        (VIEW_INVOICE_DETAILS, "Viewed Invoice Details"),
        (STRIPE_WEBHOOK_RECEIVED, "Stripe Webhook Received"),
        (STRIPE_WEBHOOK_ERROR, "Stripe Webhook Error"),
        (DEBUG_SUBSCRIPTION_DATA, "Debug Subscription Data"),
        (DEBUG_PAYMENT_DATA, "Debug Payment Data"),
        (RETRY_SUBSCRIPTION_PAYMENT, "Retried Subscription Payment"),
        (SYNC_SUBSCRIPTION_STATUS, "Synced Subscription Status"),
        (CHECK_ALL_SUBSCRIPTIONS, "Checked All Subscriptions"),
        (SYNC_ALL_SUBSCRIPTIONS, "Synced All Subscriptions"),
        
        
        (TWO_FACTOR_ENABLED, "2FA Enabled"),
        (TWO_FACTOR_DISABLED, "2FA Disabled"),
        (LOGIN_FAILED, "Login Failed"),
        (ACCOUNT_LOCKED, "Account Locked"),
        
        (COMPANY_CREATED, "Company Created"),
        (COMPANY_UPDATED, "Company Updated"),
        (COMPANY_VERIFIED, "Company Verified"),
        
        (TEAM_MEMBER_INVITED, "Team Member Invited"),
        (TEAM_MEMBER_JOINED, "Team Member Joined"),
        (TEAM_MEMBER_REMOVED, "Team Member Removed"),
        (TEAM_MEMBER_UPDATED, "Team Member Updated"),
        
        (INVOICE_GENERATED, "Invoice Generated"),
        (PAYMENT_METHOD_ADDED, "Payment Method Added"),
        (PAYMENT_METHOD_REMOVED, "Payment Method Removed"),
        
        (SYSTEM_CONFIG_CHANGED, "System Config Changed"),
        (API_KEY_CREATED, "API Key Created"),
        (API_KEY_REVOKED, "API Key Revoked"),
    ]


class AuditLogSeverity:
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    
    CHOICES = [
        (INFO, "Info"),
        (WARNING, "Warning"),
        (ERROR, "Error"),
        (CRITICAL, "Critical"),
    ]

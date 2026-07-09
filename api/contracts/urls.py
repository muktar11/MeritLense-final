from django.urls import path

from .views import (
    AgreementAuditTrailView,
    AgreementCheckboxAcceptanceView,
    AgreementConfirmSigningView,
    AgreementDownloadView,
    AgreementInitiateSigningView,
    AgreementResendOtpView,
    AgreementStatusView,
    AgreementVerifyView,
    AgreementVersionCheckView,
    CookieConsentCreateView,
    CookieConsentStatusView,
    SessionDeviceCheckView,
    SessionPrivacyNoticeView,
    SessionVerbalConfirmationView,
)


urlpatterns = [
    path("agreements/accept", AgreementCheckboxAcceptanceView.as_view(), name="agreements-accept"),
    path("agreements/sign/initiate", AgreementInitiateSigningView.as_view(), name="agreements-sign-initiate"),
    path("agreements/sign/confirm", AgreementConfirmSigningView.as_view(), name="agreements-sign-confirm"),
    path("agreements/sign/resend", AgreementResendOtpView.as_view(), name="agreements-sign-resend"),
    path("agreements/status/<str:user_id>", AgreementStatusView.as_view(), name="agreements-status"),
    path("agreements/download/<str:id>", AgreementDownloadView.as_view(), name="agreements-download"),
    path("agreements/verify/<str:id>", AgreementVerifyView.as_view(), name="agreements-verify"),
    path("agreements/audit/<str:id>", AgreementAuditTrailView.as_view(), name="agreements-audit"),
    path("agreements/version-check", AgreementVersionCheckView.as_view(), name="agreements-version-check"),
    path("cookies/consent", CookieConsentCreateView.as_view(), name="cookies-consent-create"),
    path("cookies/consent/<str:user_id>", CookieConsentStatusView.as_view(), name="cookies-consent-status"),
    path("candidate/verbal-confirmation", SessionVerbalConfirmationView.as_view(), name="candidate-verbal-confirmation"),
    path("candidate/privacy-notice", SessionPrivacyNoticeView.as_view(), name="candidate-privacy-notice"),
    path("candidate/device-check", SessionDeviceCheckView.as_view(), name="candidate-device-check"),
]

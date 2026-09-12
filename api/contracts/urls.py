from django.urls import path

from .views import (
    AgreementAcceptView,
    AgreementSignInitiateView,
    AgreementSignResendView,
    AgreementSignConfirmView,
    agreement_status,
    agreement_download,
    agreement_versions,
    agreement_preview,
    agreement_public_preview,
    agreement_verify,
    agreement_audit,
    admin_user_agreements,
)

urlpatterns = [
    path('accept', AgreementAcceptView.as_view(), name='agreement-accept'),
    path('sign/initiate', AgreementSignInitiateView.as_view(), name='agreement-sign-initiate'),
    path('sign/resend', AgreementSignResendView.as_view(), name='agreement-sign-resend'),
    path('sign/confirm', AgreementSignConfirmView.as_view(), name='agreement-sign-confirm'),
    path('status', agreement_status, name='agreement-status'),
    path('download/<str:agreement_id>', agreement_download, name='agreement-download'),
    path('versions', agreement_versions, name='agreement-versions'),
    path('preview/<str:agreement_type>', agreement_preview, name='agreement-preview'),
    path('public-preview/<str:agreement_type>', agreement_public_preview, name='agreement-public-preview'),
    path('verify/<str:contract_id>', agreement_verify, name='agreement-verify'),
    path('audit/<str:agreement_id>', agreement_audit, name='agreement-audit'),
    path('admin/user/<str:user_id>', admin_user_agreements, name='agreement-admin-user-list'),
]

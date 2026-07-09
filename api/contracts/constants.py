from api.core.constants import AgreementType

# Source of truth for "current" agreement versions (Section 1 of the Contract &
# Agreement Management Guide v1.2). The frontend fetches these rather than
# hardcoding them, so a version bump is a one-line change here.
CURRENT_VERSIONS = {
    AgreementType.PRIVACY_TERMS: "v1.1",
    AgreementType.AI_DISCLOSURE: "v1.0",
    AgreementType.B2B_AGREEMENT: "Legal",
    AgreementType.DPA: "v1.2",
    AgreementType.B2C_AGREEMENT: "v1.1",
    AgreementType.CANDIDATE_CONSENT: "v1.1",
}

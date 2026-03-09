def get_company_team_members(company):
    from api.accounts.models import TeamMemberProfile
    
    team_profiles = TeamMemberProfile.objects.filter(
        company=company
    ).select_related('user')
    
    return [profile.user for profile in team_profiles]


def get_company_team_member_ids(company):
    return [user.id for user in get_company_team_members(company)]
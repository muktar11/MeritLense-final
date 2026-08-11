from django.urls import path

from api.dashboard import views_admin
from . import views_b2b
from . import views_b2c

urlpatterns = [
    path('b2b/stats', views_b2b.B2BDashboardStatsView.as_view(), name='b2b-stats'),
    path('b2b/candidates/recent', views_b2b.B2BRecentCandidatesView.as_view(), name='b2b-recent-candidates'),
    path('b2b/evaluations/recent', views_b2b.B2BRecentEvaluationsView.as_view(), name='b2b-recent-evaluations'),
    path('b2b/score-distribution', views_b2b.B2BScoreDistributionView.as_view(), name='b2b-score-distribution'),
    path('b2b/evaluation-trend', views_b2b.B2BEvaluationTrendView.as_view(), name='b2b-evaluation-trend'),
    path('b2b/language-distribution', views_b2b.B2BLanguageDistributionView.as_view(), name='b2b-language-distribution'),
    path('b2b/performance-metrics', views_b2b.B2BPerformanceMetricsView.as_view(), name='b2b-performance-metrics'),
    path('b2b/status-distribution', views_b2b.B2BEvaluationStatusDistributionView.as_view(), name='b2b-status-distribution'),
    path('b2b/monthly-activity', views_b2b.B2BMonthlyActivityView.as_view(), name='b2b-monthly-activity'),
    path('b2b/candidate-comparison', views_b2b.B2BCandidateComparisonView.as_view(), name='b2b-candidate-comparison'),

    path('b2c/stats', views_b2c.B2CDashboardStatsView.as_view(), name='b2c-stats'),
    path('b2c/candidates/recent', views_b2c.B2CRecentCandidatesView.as_view(), name='b2c-recent-candidates'),
    path('b2c/evaluations/recent', views_b2c.B2CRecentEvaluationsView.as_view(), name='b2c-recent-evaluations'),
    path('b2c/candidate-comparison', views_b2c.B2CCandidateComparisonView.as_view(), name='b2c-candidate-comparison'),
    path('b2c/evaluation-time-range', views_b2c.B2CEvaluationTimeRangeView.as_view(), name='b2c-evaluation-time-range'),
    path('b2c/status-distribution', views_b2c.B2CEvaluationStatusDistributionView.as_view(), name='b2c-status-distribution'),
    path('b2c/job-role-distribution', views_b2c.B2CJobRoleDistributionView.as_view(), name='b2c-job-role-distribution'),
    path('b2c/score-trend', views_b2c.B2CScoreTrendView.as_view(), name='b2c-score-trend'),

    path('admin/stats', views_admin.AdminDashboardStatsView.as_view(), name='admin-stats'),
    path('admin/system-load', views_admin.AdminSystemLoadTrendView.as_view(), name='admin-system-load'),
    path('admin/user-growth', views_admin.AdminUserGrowthTrendView.as_view(), name='admin-user-growth'),
    path('admin/evaluation-types', views_admin.AdminEvaluationTypesDistributionView.as_view(), name='admin-evaluation-types'),
    path('admin/package-contribution', views_admin.AdminPackageContributionView.as_view(), name='admin-package-contribution'),
    path('admin/status-distribution', views_admin.AdminEvaluationStatusDistributionView.as_view(), name='admin-status-distribution'),
    path('admin/user-type-distribution', views_admin.AdminUserTypeDistributionView.as_view(), name='admin-user-type-distribution'),
    path('admin/revenue-trend', views_admin.AdminRevenueTrendView.as_view(), name='admin-revenue-trend'),
    path('admin/top-candidates', views_admin.AdminTopPerformingCandidatesView.as_view(), name='admin-top-candidates'),
    path('admin/recent-activity', views_admin.AdminRecentActivityView.as_view(), name='admin-recent-activity'),
    path('admin/geographic-distribution', views_admin.AdminGeographicDistributionView.as_view(), name='admin-geographic-distribution'),
]

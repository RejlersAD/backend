from django.urls import path
from .views import (
    UsageOverviewView,
    DisciplineUsageView,
    TopUsersView,
    TrendsView,
    ActiveNowView,
    AllUsersView,
    DatabaseEventsView,
    UserSessionsView,
)

app_name = 'usage_tracking'

urlpatterns = [
    path('overview/',    UsageOverviewView.as_view(),    name='overview'),
    path('disciplines/', DisciplineUsageView.as_view(),  name='disciplines'),
    path('top-users/',   TopUsersView.as_view(),         name='top-users'),
    path('trends/',      TrendsView.as_view(),            name='trends'),
    path('active-now/',  ActiveNowView.as_view(),         name='active-now'),
    path('all-users/',   AllUsersView.as_view(),          name='all-users'),
    path('db-events/',   DatabaseEventsView.as_view(),   name='db-events'),
    path('sessions/',    UserSessionsView.as_view(),     name='sessions'),
]

from django.urls import path

from . import views

app_name = 'timesheet'

urlpatterns = [
    # Setup / health
    path('health/',                views.health,           name='health'),

    # Discovery wizard
    path('discovery/databases/',   views.databases,        name='databases'),
    path('discovery/tables/',      views.tables,           name='tables'),
    path('discovery/columns/',     views.columns,          name='columns'),
    path('discovery/preview/',     views.preview,          name='preview'),

    # Reports
    path('live/',                  views.live,             name='live'),
    path('daily/',                 views.daily,            name='daily'),
    path('monthly/',               views.monthly,          name='monthly'),
    path('user/',                  views.user_drill,       name='user'),
    path('lookup-by-code/',        views.lookup_by_code,   name='lookup-by-code'),

    # Exports
    path('export/daily/',          views.export_daily_excel,   name='export-daily'),
    path('export/monthly/',        views.export_monthly_excel, name='export-monthly'),
    path('export/monthly/pdf/',    views.export_monthly_pdf,   name='export-monthly-pdf'),

    # Mirror ingest — office-side sync agent POSTs batched events here.
    # Auth via X-Timesheet-Mirror-Key header (TIMESHEET_MIRROR_API_KEY env var).
    path('mirror/ingest/',         views.ingest_events,        name='mirror-ingest'),
    path('mirror/ingest-users/',   views.ingest_users,         name='mirror-ingest-users'),
]

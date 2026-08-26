"""Valve Standards — URL routing."""
from django.urls import path

from . import views

app_name = 'valve_standards'

urlpatterns = [
    path('config/', views.config_view, name='config'),
    path('material-groups/', views.material_group_list, name='material-groups'),
    path('material-groups/<str:group_no>/', views.material_group_detail, name='material-group-detail'),
    path('ratings/', views.rating_list, name='ratings'),
    path('wall-thickness/', views.wall_thickness_list, name='wall-thickness'),
    path('wall-thickness-socketweld/', views.wall_thickness_socketweld_list, name='wall-thickness-socketweld'),
    path('nps-to-id/', views.nps_to_id_list, name='nps-to-id'),
    path('reference-standards/', views.reference_standard_list, name='reference-standards'),
    path('validate/', views.validate_rating, name='validate'),

    # ── ASME B31.3 ────────────────────────────────────────────────────────
    path('b31-3/config/', views.b313_config_view, name='b313-config'),
    path('b31-3/material-allowable-stress/', views.material_allowable_stress_list, name='b313-material-allowable-stress'),
    path('b31-3/material-allowable-stress/<int:pk>/', views.material_allowable_stress_detail, name='b313-material-allowable-stress-detail'),
    path('b31-3/high-pressure-allowable-stress/', views.high_pressure_allowable_stress_list, name='b313-high-pressure-allowable-stress'),
    path('b31-3/casting-quality-factors/', views.casting_quality_factor_list, name='b313-casting-quality-factors'),
    path('b31-3/weld-joint-quality-factors/', views.weld_joint_quality_factor_list, name='b313-weld-joint-quality-factors'),
    path('b31-3/thermal-expansion/', views.thermal_expansion_coefficient_list, name='b313-thermal-expansion'),
    path('b31-3/modulus-of-elasticity/', views.modulus_of_elasticity_list, name='b313-modulus-of-elasticity'),

    # ── ASME B16.5 ────────────────────────────────────────────────────────
    path('b16-5/config/', views.b165_config_view, name='b165-config'),
    path('b16-5/material-groups/', views.b165_material_group_list, name='b165-material-groups'),
    path('b16-5/material-groups/<str:group_no>/', views.b165_material_group_detail, name='b165-material-group-detail'),
    path('b16-5/ratings/', views.b165_rating_list, name='b165-ratings'),
    path('b16-5/drilling-templates/', views.b165_drilling_template_list, name='b165-drilling-templates'),
    path('b16-5/flange-dimensions/', views.b165_flange_dimension_list, name='b165-flange-dimensions'),
    path('b16-5/flange-bolting-recommendations/', views.b165_bolting_recommendation_list, name='b165-flange-bolting-recommendations'),
]

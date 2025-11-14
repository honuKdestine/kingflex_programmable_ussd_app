from django.urls import path
from . import views


urlpatterns = [
    path("/interaction", views.interaction, name="interaction"),
    path("/fulfillment", views.fulfillment, name="fulfillment"),
]

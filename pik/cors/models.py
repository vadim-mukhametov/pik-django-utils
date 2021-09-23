from django.core.cache import caches
from django.db import models
from django.utils.translation import gettext_lazy as _
from pik.core.models import BasePHistorical

from .consts import DEFAULT_CACHE_NAME, CACHE_KEY


class Cors(BasePHistorical):
    permitted_fields = {'{app_label}.change_{model_name}': ['cors']}

    cors = models.CharField(max_length=255, unique=True, help_text=_(
        "Название домена допущенного делать междоменные запросы, например: "
        "staff-front.pik-software.ru или localhost:3000"))

    class Meta:
        ordering = ('-created',)
        verbose_name = _("Разрешение на кросс-доменные запросы")
        verbose_name_plural = _("Разрешения на кросс-доменные запросы")

    def __str__(self):
        return self.cors

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        key = CACHE_KEY.format(**{'domain': self.cors})
        caches[DEFAULT_CACHE_NAME].delete(key)

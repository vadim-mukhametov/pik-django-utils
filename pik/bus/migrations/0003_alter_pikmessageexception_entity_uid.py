# Generated by Django 3.2.16 on 2023-01-20 12:38

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bus', '0002_auto_20220527_1129'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pikmessageexception',
            name='entity_uid',
            field=models.UUIDField(blank=True, null=True, unique=True, verbose_name='Идентификатор сущности'),
        ),
    ]

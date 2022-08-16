# Generated by Django 3.2.12 on 2022-05-27 11:29

import django.contrib.postgres.indexes
from django.db import migrations, models
import django.db.models.expressions


class Migration(migrations.Migration):

    dependencies = [
        ('bus', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='pikmessageexception',
            name='has_dependencies',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Имеет зависимости'),
        ),
        migrations.AlterField(
            model_name='pikmessageexception',
            name='exception_type',
            field=models.CharField(db_index=True, max_length=255, verbose_name='Тип ошибки'),
        ),
        migrations.AlterField(
            model_name='pikmessageexception',
            name='queue',
            field=models.CharField(db_index=True, max_length=255, verbose_name='Очередь'),
        ),
        migrations.AddIndex(
            model_name='pikmessageexception',
            index=django.contrib.postgres.indexes.GinIndex(
                django.db.models.expressions.F('dependencies'),
                name='dependencies_indx_2412'),
        ),
        migrations.AddIndex(
            model_name='pikmessageexception',
            index=models.Index(fields=['has_dependencies', 'queue'], name='bus_pikmess_has_dep_72351b_idx'),
        ),
        migrations.AddIndex(
            model_name='pikmessageexception',
            index=models.Index(fields=['queue'], name='bus_pikmess_queue_6d2d53_idx'),
        ),
        migrations.RunSQL(
            "UPDATE bus_pikmessageexception SET has_dependencies = TRUE WHERE dependencies = '{}'",
            "UPDATE bus_pikmessageexception SET has_dependencies = FALSE")
    ]

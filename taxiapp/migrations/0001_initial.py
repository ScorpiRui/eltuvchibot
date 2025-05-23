# Generated by Django 4.2 on 2025-04-19 19:56

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Driver",
            fields=[
                ("tg_id", models.BigIntegerField(primary_key=True, serialize=False)),
                ("api_id", models.PositiveIntegerField()),
                ("api_hash", models.CharField(max_length=64)),
                ("bot_token", models.CharField(max_length=128)),
                ("session", models.TextField()),
                ("active", models.BooleanField(default=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
